from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from queue import Empty, Full, Queue
import re
import time
from typing import Callable

from aiogram import Bot
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from audience import TelegramAudience
from config import Settings
from site_cooldown import (
    activate_site_cooldown,
    clear_site_cooldown,
    format_remaining_time,
    load_site_cooldown,
)


CANCEL_RECOVERY = "__CANCEL_SESSION_RECOVERY__"
SMS_CODE_PATTERN = re.compile(r"^\d{4}$")
PIN_INPUT_SELECTOR = '[data-testid="auth_pin_input"]'
PIN_EDITABLE_SELECTOR = (
    'input[data-testid="auth_pin_input"], '
    'textarea[data-testid="auth_pin_input"], '
    '[contenteditable="true"][data-testid="auth_pin_input"]'
)
PIN_DESCENDANT_SELECTOR = (
    '[data-testid="auth_pin_input"] input, '
    '[data-testid="auth_pin_input"] textarea, '
    '[data-testid="auth_pin_input"] [contenteditable="true"]'
)
OTP_SEMANTIC_SELECTOR = (
    'input[autocomplete="one-time-code"], '
    'input[name*="pin" i], '
    'input[id*="pin" i], '
    'input[inputmode="numeric"][maxlength="4"]'
)
SMS_LOGIN_BUTTON_SELECTOR = '[data-testid="enter_with_sms_btn"]'
SMS_LOGIN_BUTTON_TEXT_PATTERN = re.compile(
    r"войти\s+по\s+сим[\s‑–—-]*пушу\s+или\s+(?:смс|sms)",
    re.IGNORECASE,
)
SMS_LOGIN_BUTTON_STABLE_POLLS = 3
LOGIN_BUTTON_RENDER_DELAY_SEC = 1.0
LOGIN_POST_CLICK_STATUS_CHECK_SEC = 1.0
OTP_FIELD_RENDER_DELAY_SEC = 1.0
OTP_FILL_DELAY_SEC = 1.0
LOGIN_RETRY_LATER_PATTERN = re.compile(
    r"(?:можно\s+будет\s+)?повтор(?:ить|ите)\s+через\s+\d+(?:[.,]\d+)?\s*"
    r"(?:(?:часов|часа|час)\b|ч\.?(?=\s|$))",
    re.IGNORECASE,
)
PHONE_INPUT_SELECTOR = (
    'input[type="tel"], '
    'input[autocomplete="tel"], '
    'input[name*="phone" i], '
    'input[inputmode="tel"]'
)


class SessionRecoveryError(RuntimeError):
    def __init__(self, message: str, *, screenshot_path: Path | None = None):
        super().__init__(message)
        self.screenshot_path = screenshot_path


class LoginRetryLaterError(SessionRecoveryError):
    """Profi.ru запретил новый запрос входа до указанного срока."""


def normalize_sms_code(value: str) -> str | None:
    code = (value or "").strip()
    return code if SMS_CODE_PATTERN.fullmatch(code) else None


def _find_login_retry_later_text(page) -> str | None:
    try:
        body_text = page.locator("body").inner_text(timeout=1_000)
    except (AttributeError, PlaywrightError):
        return None
    match = LOGIN_RETRY_LATER_PATTERN.search(" ".join(body_text.split()))
    return match.group(0) if match else None


def _raise_if_login_retry_later(page) -> None:
    retry_text = _find_login_retry_later_text(page)
    if retry_text:
        raise LoginRetryLaterError(
            f"Profi.ru ограничил повторный вход: «{retry_text}»"
        )


def _watch_for_login_retry_later(page, duration_sec: float) -> None:
    deadline = time.monotonic() + duration_sec
    while time.monotonic() < deadline:
        _raise_if_login_retry_later(page)
        time.sleep(0.1)
    _raise_if_login_retry_later(page)


def _save_recovery_debug(
    page,
    settings: Settings,
    *,
    phase: str,
    error: Exception,
    failed_requests: list[str],
) -> Path:
    settings.debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = settings.debug_dir / "session_recovery_failed.png"
    html_path = settings.debug_dir / "session_recovery_failed.html"
    details_path = settings.debug_dir / "session_recovery_failed.txt"
    for path in (screenshot_path, html_path, details_path):
        with suppress(OSError):
            path.unlink()
    with suppress(Exception):
        page.screenshot(path=str(screenshot_path), full_page=True)
        screenshot_path.chmod(0o600)
    with suppress(Exception):
        html_path.write_text(page.content(), encoding="utf-8")
        html_path.chmod(0o600)
    with suppress(Exception):
        current_url = page.url
        title = page.title()
        details_path.write_text(
            f"Этап: {phase}\n"
            f"Ошибка: {type(error).__name__}: {error}\n"
            f"URL: {current_url}\n"
            f"Заголовок: {title}\n"
            "Неудачные сетевые запросы:\n"
            + ("\n".join(failed_requests[-30:]) or "нет данных")
            + "\n",
            encoding="utf-8",
        )
        details_path.chmod(0o600)
    return screenshot_path


def _usable_sms_inputs(root, selector: str) -> list:
    selectors = [
        PIN_EDITABLE_SELECTOR,
        PIN_DESCENDANT_SELECTOR,
        PIN_INPUT_SELECTOR,
    ]
    if selector not in selectors:
        selectors.append(selector)
    if OTP_SEMANTIC_SELECTOR not in selectors:
        selectors.append(OTP_SEMANTIC_SELECTOR)

    for candidate_selector in selectors:
        inputs = root.locator(candidate_selector)
        usable = []
        for index in range(inputs.count()):
            candidate = inputs.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                if (
                    hasattr(candidate, "is_enabled")
                    and not candidate.is_enabled()
                ):
                    continue
                if (
                    hasattr(candidate, "is_editable")
                    and not candidate.is_editable()
                ):
                    continue
            except PlaywrightError:
                continue
            usable.append(candidate)
        if usable:
            return usable
    return []


def _candidate_pages(page) -> list:
    pages = [page]
    try:
        pages.extend(page.context.pages)
    except (AttributeError, PlaywrightError):
        pass

    unique_pages = []
    seen_ids: set[int] = set()
    for candidate in pages:
        candidate_id = id(candidate)
        if candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        try:
            if hasattr(candidate, "is_closed") and candidate.is_closed():
                continue
        except PlaywrightError:
            continue
        unique_pages.append(candidate)
    return unique_pages


def _find_sms_code_root(page, selector: str):
    roots = []
    for candidate_page in _candidate_pages(page):
        roots.append(candidate_page)
        roots.extend(
            frame
            for frame in getattr(candidate_page, "frames", [])
            if frame is not getattr(candidate_page, "main_frame", None)
        )
    for root in roots:
        try:
            if _usable_sms_inputs(root, selector):
                return root
        except PlaywrightError:
            continue
    return None


def _first_visible(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _visible_phone_input(page):
    """Ищет только поле телефона на основной странице Profi.ru."""
    try:
        login_input = _first_visible(page.get_by_test_id("auth_login_input"))
        if login_input is None:
            login_input = _first_visible(page.locator(PHONE_INPUT_SELECTOR))
        return login_input
    except (AttributeError, PlaywrightError):
        return None


def _button_text(control) -> str:
    try:
        return " ".join(control.inner_text().split())
    except (AttributeError, PlaywrightError):
        return ""


def _visible_sms_login_button(page):
    """Возвращает data-testid только после превращения в нужную SMS-кнопку."""
    try:
        controls = page.locator(SMS_LOGIN_BUTTON_SELECTOR)
        for index in range(controls.count()):
            control = controls.nth(index)
            if (
                control.is_visible()
                and control.is_enabled()
                and SMS_LOGIN_BUTTON_TEXT_PATTERN.search(_button_text(control))
            ):
                return control
    except (AttributeError, PlaywrightError):
        return None
    return None


def _wait_for_phone_input(page, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        _raise_if_login_retry_later(page)
        login_input = _visible_phone_input(page)
        if login_input is not None:
            return login_input
        time.sleep(0.25)
    return None


def _fill_login_input(login_input, login: str) -> None:
    # Возвращён проверенный способ из версий до 83f8e3b: Playwright сам
    # устанавливает значение поля и отправляет события input/change.
    login_input.fill(login)


def _wait_for_stable_sms_login_button(page, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    stable_polls = 0
    latest = None
    while time.monotonic() < deadline:
        _raise_if_login_retry_later(page)
        control = _visible_sms_login_button(page)
        if control is None:
            stable_polls = 0
            latest = None
        else:
            stable_polls += 1
            latest = control
            if stable_polls >= SMS_LOGIN_BUTTON_STABLE_POLLS:
                return latest
        time.sleep(0.1)
    return None


def _click_verified_sms_login_button(control) -> None:
    text = _button_text(control)
    if (
        not control.is_visible()
        or not control.is_enabled()
        or not SMS_LOGIN_BUTTON_TEXT_PATTERN.search(text)
    ):
        raise SessionRecoveryError(
            "Перед кликом кнопка перестала быть входом по сим-пушу или СМС"
        )
    control.click()


def _wait_for_sms_code_root(page, selector: str, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        _raise_if_login_retry_later(page)
        root = _find_sms_code_root(page, selector)
        if root is not None:
            return root
        time.sleep(0.25)
    return None


def _wait_for_sms_code_to_close(page, selector: str, timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        _raise_if_login_retry_later(page)
        if _find_sms_code_root(page, selector) is None:
            return True
        time.sleep(0.25)
    return False


def _looks_like_login_page(page) -> bool:
    if _visible_phone_input(page) is not None:
        return True
    try:
        title = page.title().casefold()
        url = page.url.casefold()
    except PlaywrightError:
        return True
    return "вход" in title or "login" in title or "/login" in url


def _fill_sms_code(root, selector: str, code: str) -> None:
    if not SMS_CODE_PATTERN.fullmatch(code):
        raise SessionRecoveryError("SMS-код должен содержать ровно 4 цифры")

    visible_inputs = _usable_sms_inputs(root, selector)

    if not visible_inputs:
        raise SessionRecoveryError("Поле для SMS-кода не найдено")

    input_locator = visible_inputs[0]
    input_locator.fill(code)

    try:
        try:
            actual_value = input_locator.input_value(timeout=500)
        except TypeError:
            actual_value = input_locator.input_value()
    except (AttributeError, PlaywrightError):
        # Поле могло исчезнуть сразу после корректного кода из-за автоотправки формы.
        return

    if re.sub(r"\D", "", actual_value) != code:
        raise SessionRecoveryError(
            "Поле auth_pin_input найдено, но не приняло переданный SMS-код"
        )


def recreate_profi_session(
    settings: Settings,
    code_provider: Callable[[], str],
    on_sms_requested: Callable[[], None],
) -> None:
    """Создаёт новый Playwright storage_state через SMS-код."""
    if not settings.profi_login:
        raise SessionRecoveryError("В .env не указан PROFI_LOGIN")

    settings.ensure_directories()
    temporary_state = settings.auth_state_path.with_name(
        f".{settings.auth_state_path.name}.recovery.tmp"
    )

    with suppress(OSError):
        temporary_state.unlink()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            **settings.playwright_launch_options(
                headless=settings.session_recovery_headless,
            )
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        phase = "открытие страницы входа"
        failed_requests: list[str] = []

        def record_failed_request(request) -> None:
            if len(failed_requests) >= 100:
                return
            safe_url = request.url.split("?", 1)[0]
            failed_requests.append(
                f"{request.method} {safe_url}: {request.failure or 'неизвестная ошибка'}"
            )

        with suppress(Exception):
            page.on("requestfailed", record_failed_request)

        try:
            page.goto(
                settings.page_url,
                wait_until="domcontentloaded",
                timeout=settings.page_timeout_ms,
            )

            phase = "ожидание поля телефона"
            login_input = _wait_for_phone_input(
                page,
                min(settings.page_timeout_ms, 30_000),
            )
            if login_input is None:
                raise SessionRecoveryError(
                    "Поле телефона auth_login_input не найдено"
                )

            phase = "ввод телефона"
            _fill_login_input(login_input, settings.profi_login)

            phase = "пауза после ввода телефона"
            time.sleep(LOGIN_BUTTON_RENDER_DELAY_SEC)
            _raise_if_login_retry_later(page)

            phase = "ожидание стабильной кнопки входа по сим-пушу или СМС"
            login_button = _wait_for_stable_sms_login_button(
                page,
                min(settings.page_timeout_ms, 30_000),
            )
            if login_button is None:
                raise SessionRecoveryError(
                    "После ввода телефона data-testid=enter_with_sms_btn "
                    "не стал кнопкой «Войти по сим-пушу или СМС»"
                )

            phase = 'проверка и нажатие data-testid="enter_with_sms_btn"'
            _raise_if_login_retry_later(page)
            _click_verified_sms_login_button(login_button)

            phase = "проверка ответа сайта после запроса входа"
            _watch_for_login_retry_later(
                page,
                LOGIN_POST_CLICK_STATUS_CHECK_SEC,
            )

            # До получения четырёх цифр не ищем поле и никак с ним не
            # взаимодействуем. Сначала только открываем приём кода в Telegram.
            on_sms_requested()

            phase = "ожидание SMS-кода из Telegram"
            code = code_provider()
            if code == CANCEL_RECOVERY:
                raise SessionRecoveryError("Восстановление отменено пользователем")
            normalized_code = normalize_sms_code(code)
            if normalized_code is None:
                raise SessionRecoveryError("Получен некорректный SMS-код")

            phase = "пауза перед первым поиском поля SMS-кода"
            time.sleep(OTP_FIELD_RENDER_DELAY_SEC)

            # Это первое обращение к форме кода. Ищем актуальное редактируемое
            # поле во всех вкладках и фреймах только после ответа пользователя.
            phase = "поиск актуального поля SMS-кода"
            otp_root = _wait_for_sms_code_root(
                page,
                settings.profi_otp_selector,
                min(settings.page_timeout_ms, 30_000),
            )
            if otp_root is None:
                raise SessionRecoveryError(
                    "После получения кода поле auth_pin_input не найдено или недоступно для ввода"
                )

            phase = "пауза перед вставкой SMS-кода"
            time.sleep(OTP_FILL_DELAY_SEC)

            phase = "ввод SMS-кода"
            _fill_sms_code(
                otp_root,
                settings.profi_otp_selector,
                normalized_code,
            )

            phase = "проверка принятия SMS-кода"
            if not _wait_for_sms_code_to_close(
                page,
                settings.profi_otp_selector,
                min(settings.page_timeout_ms, 20_000),
            ):
                raise SessionRecoveryError(
                    "После ввода кода поле SMS осталось открытым. "
                    "Код мог быть неверным или просроченным."
                )

            phase = "проверка успешного входа"
            cards_found = False
            try:
                page.wait_for_selector(
                    settings.card_selector,
                    state="attached",
                    timeout=min(settings.page_timeout_ms, 5_000),
                )
                cards_found = True
            except PlaywrightTimeoutError:
                page.goto(
                    settings.page_url,
                    wait_until="domcontentloaded",
                    timeout=settings.page_timeout_ms,
                )
                time.sleep(1)

            # Отсутствие карточек может означать, что сейчас просто нет заказов.
            # Ошибкой считаем возврат формы входа, а не пустую доску заказов.
            if not cards_found and _looks_like_login_page(page):
                raise SessionRecoveryError(
                    "После SMS-кода Profi.ru снова показал страницу входа"
                )

            context.storage_state(path=str(temporary_state))
            temporary_state.chmod(0o600)
            temporary_state.replace(settings.auth_state_path)
        except Exception as exc:
            screenshot_path = _save_recovery_debug(
                page,
                settings,
                phase=phase,
                error=exc,
                failed_requests=failed_requests,
            )
            if isinstance(exc, LoginRetryLaterError):
                raise LoginRetryLaterError(
                    f"Этап «{phase}»: {exc}",
                    screenshot_path=screenshot_path,
                ) from exc
            if isinstance(exc, SessionRecoveryError):
                raise SessionRecoveryError(
                    f"Этап «{phase}»: {exc}",
                    screenshot_path=screenshot_path,
                ) from exc
            error_text = str(exc).splitlines()[0].strip() or type(exc).__name__
            raise SessionRecoveryError(
                f"Этап «{phase}»: {error_text}",
                screenshot_path=screenshot_path,
            ) from exc
        finally:
            browser.close()
            with suppress(OSError):
                temporary_state.unlink()


class SessionRecoveryManager:
    def __init__(
        self,
        settings: Settings,
        bot: Bot,
        log,
        on_success: Callable[[], None] | None = None,
        audience: TelegramAudience | None = None,
    ):
        self.settings = settings
        self.bot = bot
        self.log = log
        self.awaiting_code = False
        self._code_queue: Queue[str] = Queue(maxsize=1)
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._session_ready = asyncio.Event()
        self._cancel_event = asyncio.Event()
        self._on_success = on_success
        self.audience = audience or TelegramAudience(settings, log)
        self._last_started_at = 0.0
        if settings.auth_state_path.exists():
            self._session_ready.set()

    @property
    def in_progress(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def site_cooldown_remaining_seconds(self) -> int:
        cooldown = load_site_cooldown(self.settings.site_cooldown_path)
        return cooldown.remaining_seconds() if cooldown is not None else 0

    async def _send(self, text: str) -> int:
        return await self.audience.send(self.bot, text)

    async def _send_required(self, text: str) -> None:
        """Не продолжает вход, пока Telegram недоступен пользователю."""
        while True:
            if not self.audience.has_recipients:
                await self.audience.wait_until_available()
            if await self._send(text):
                return
            self.log.warning(
                "Telegram недоступен; откладываю следующий шаг входа на 5 секунд"
            )
            await asyncio.sleep(5)

    def _clear_code_queue(self) -> None:
        while True:
            try:
                self._code_queue.get_nowait()
            except Empty:
                return

    async def start(self, reason: str, *, bypass_cooldown: bool = False) -> bool:
        async with self._lock:
            if not self.settings.session_recovery_enabled:
                await self._send(
                    "⚠️ Автовосстановление сессии отключено. "
                    "Включите SESSION_RECOVERY_ENABLED в .env."
                )
                return False
            if self.in_progress:
                remaining = self.site_cooldown_remaining_seconds
                if remaining:
                    await self._send(
                        "⏸ Действует обязательная пауза Profi.ru. "
                        f"Осталось: {format_remaining_time(remaining)}."
                    )
                else:
                    await self._send("ℹ️ Обновление сессии уже выполняется.")
                return False

            elapsed = time.monotonic() - self._last_started_at
            if not bypass_cooldown and elapsed < self.settings.recovery_cooldown_sec:
                remaining = max(1, int(self.settings.recovery_cooldown_sec - elapsed))
                await self._send(
                    "⏳ Повторный запрос SMS временно ограничен. "
                    f"Попробуйте через {remaining} сек."
                )
                return False

            self._clear_code_queue()
            self.awaiting_code = False
            self._cancel_event.clear()
            self._session_ready.clear()
            self._last_started_at = time.monotonic()
            self._task = asyncio.create_task(self._run(reason))
            return True

    async def _announce_sms_request(self) -> None:
        if not self._code_queue.empty():
            await self._send(
                "📲 Profi.ru открыл этап подтверждения. Отправленный ранее "
                "4-значный код уже получен и будет введён после появления поля."
            )
            return
        self.awaiting_code = True
        await self._send(
            "📲 Profi.ru открыл этап подтверждения. Как только код придёт, "
            "отправьте боту ровно 4 цифры "
            f"в течение {self.settings.sms_code_timeout_sec // 60} мин. "
            "После получения браузер вставит код целиком в поле подтверждения. "
            "Если пришло несколько сообщений, отправьте самый последний код.\n\n"
            "Для отмены используйте /cancel."
        )

    def _wait_for_code(self) -> str:
        try:
            return self._code_queue.get(timeout=self.settings.sms_code_timeout_sec)
        except Empty as exc:
            raise SessionRecoveryError("Время ожидания SMS-кода истекло") from exc

    async def _wait_for_site_cooldown(self, *, announce: bool = True) -> bool:
        cooldown = load_site_cooldown(self.settings.site_cooldown_path)
        if cooldown is None:
            return True

        remaining = cooldown.remaining_seconds()
        resume_at = time.strftime(
            "%d.%m.%Y %H:%M:%S",
            time.localtime(cooldown.until_timestamp),
        )
        if announce:
            await self._send_required(
                "⏸ Profi.ru сообщил о слишком большом количестве попыток входа.\n\n"
                "Новые клики, запросы SMS и обращения парсера к сайту остановлены. "
                f"Осталось: {format_remaining_time(remaining)}.\n"
                f"Автоматическое возобновление: {resume_at}.\n\n"
                "Команды /renew и /resume не снимают это ограничение раньше срока."
            )
        remaining = cooldown.remaining_seconds()
        if remaining <= 0:
            clear_site_cooldown(self.settings.site_cooldown_path)
            return True
        try:
            await asyncio.wait_for(
                self._cancel_event.wait(),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            clear_site_cooldown(self.settings.site_cooldown_path)
            self._clear_code_queue()
            await self._send(
                "▶️ 12-часовая пауза завершена. Автоматически повторяю вход в Profi.ru."
            )
            return True
        return False

    async def _run(self, reason: str) -> None:
        if not self.audience.has_recipients:
            self.log.warning(
                "Ожидаю первого пользователя Telegram перед запросом SMS-кода"
            )
            await self.audience.wait_until_available()
        loop = asyncio.get_running_loop()

        def announce_from_thread() -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._announce_sms_request(),
                loop,
            )
            future.result(timeout=30)

        cooldown_announced = False
        while not self._cancel_event.is_set():
            if not await self._wait_for_site_cooldown(
                announce=not cooldown_announced,
            ):
                return
            cooldown_announced = False
            await self._send_required(
                "🔐 Сессия Profi.ru требует обновления.\n"
                f"Причина: {reason}\n\n"
                "Ввожу номер телефона и нажимаю только кнопку входа "
                'data-testid="enter_with_sms_btn".'
            )
            try:
                await asyncio.to_thread(
                    recreate_profi_session,
                    self.settings,
                    self._wait_for_code,
                    announce_from_thread,
                )
                self._session_ready.set()
                if self._on_success is not None:
                    self._on_success()
                await self._send(
                    "✅ Сессия Profi.ru обновлена. Парсер автоматически возобновляет работу."
                )
                self.log.info("Сессия Profi.ru успешно обновлена")
                return
            except SessionRecoveryError as exc:
                self.log.error("Не удалось обновить сессию Profi.ru: %s", exc)
                if exc.screenshot_path is not None and exc.screenshot_path.exists():
                    try:
                        await self.audience.send_photo(
                            self.bot,
                            str(exc.screenshot_path),
                            "Диагностика неудачного входа в Profi.ru. "
                            "На изображении видно состояние страницы в момент ошибки.",
                        )
                    except Exception:
                        self.log.exception(
                            "Не удалось отправить диагностический скриншот в Telegram"
                        )
                if isinstance(exc, LoginRetryLaterError):
                    activate_site_cooldown(
                        self.settings.site_cooldown_path,
                        str(exc),
                    )
                    self.awaiting_code = False
                    self._clear_code_queue()
                    reason = "завершилась обязательная 12-часовая пауза"
                    await self._send(
                        "⏳ Profi.ru временно ограничил повторный вход на 12 часов.\n"
                        f"Сообщение сайта: {exc}\n\n"
                        "Бот не будет нажимать кнопки, запрашивать SMS или обращаться "
                        "к сайту до окончания срока. После паузы работа продолжится "
                        "автоматически."
                    )
                    cooldown_announced = True
                    continue
                await self._send(
                    "❌ Не удалось обновить сессию Profi.ru.\n"
                    f"Причина: {exc}\n\n"
                    "Подробности сохранены в logs/debug/session_recovery_failed.txt и .html. "
                    "Исправьте настройки при необходимости и отправьте /renew для повтора."
                )
                return
            except Exception:
                self.log.exception("Непредвиденная ошибка восстановления сессии")
                await self._send(
                    "❌ Внутренняя ошибка восстановления сессии. "
                    "Отправьте /renew для повторной попытки."
                )
                return
            finally:
                self.awaiting_code = False

    async def submit_code(self, raw_code: str) -> tuple[bool, str]:
        remaining = self.site_cooldown_remaining_seconds
        if remaining:
            return (
                False,
                "Сейчас действует обязательная пауза Profi.ru. Код не принят. "
                f"Осталось: {format_remaining_time(remaining)}.",
            )
        if not self.awaiting_code and not self.in_progress:
            return False, "Сейчас бот не ожидает SMS-код. Используйте /renew."

        code = normalize_sms_code(raw_code)
        if code is None:
            return False, "Код должен содержать ровно 4 цифры."

        try:
            self._code_queue.put_nowait(code)
        except Full:
            return False, "Код уже получен и обрабатывается."

        received_early = not self.awaiting_code
        self.awaiting_code = False
        if received_early:
            return True, "Код получен и сохранён. Введу его, когда поле появится на Profi.ru…"
        return True, "Код получен. Вставляю его в поле подтверждения Profi.ru…"

    async def cancel(self, *, force: bool = False) -> bool:
        if not self.in_progress:
            return False
        if self.site_cooldown_remaining_seconds and not force:
            return False
        self._cancel_event.set()
        with suppress(Full):
            self._code_queue.put_nowait(CANCEL_RECOVERY)
        self.awaiting_code = False
        return True

    async def wait_until_ready(self) -> None:
        await self._session_ready.wait()

    async def stop(self) -> None:
        await self.cancel(force=True)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception(
                    "Ошибка завершения процедуры восстановления сессии"
                )
