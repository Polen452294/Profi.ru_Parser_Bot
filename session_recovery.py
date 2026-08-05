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


CANCEL_RECOVERY = "__CANCEL_SESSION_RECOVERY__"
SMS_CODE_PATTERN = re.compile(r"^\d{4,8}$")


class SessionRecoveryError(RuntimeError):
    def __init__(self, message: str, *, screenshot_path: Path | None = None):
        super().__init__(message)
        self.screenshot_path = screenshot_path


def normalize_sms_code(value: str) -> str | None:
    code = re.sub(r"[\s-]", "", value or "")
    return code if SMS_CODE_PATTERN.fullmatch(code) else None


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


def _visible_sms_inputs(root, selector: str) -> list:
    inputs = root.locator(selector)
    return [
        inputs.nth(index)
        for index in range(inputs.count())
        if inputs.nth(index).is_visible()
    ]


def _find_sms_code_root(page, selector: str):
    roots = [page]
    roots.extend(
        frame
        for frame in getattr(page, "frames", [])
        if frame is not getattr(page, "main_frame", None)
    )
    for root in roots:
        try:
            if _visible_sms_inputs(root, selector):
                return root
        except PlaywrightError:
            continue
    return None


def _wait_for_sms_code_root(page, selector: str, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        root = _find_sms_code_root(page, selector)
        if root is not None:
            return root
        time.sleep(0.25)
    return None


def _fill_sms_code(root, selector: str, code: str) -> None:
    visible_inputs = _visible_sms_inputs(root, selector)

    if not visible_inputs:
        raise SessionRecoveryError("Поле для SMS-кода не найдено")

    if len(visible_inputs) == 1:
        visible_inputs[0].fill(code)
        with suppress(PlaywrightError):
            visible_inputs[0].press("Enter")
        return

    if len(visible_inputs) < len(code):
        raise SessionRecoveryError("Количество полей не соответствует длине SMS-кода")

    for input_locator, digit in zip(visible_inputs, code):
        input_locator.fill(digit)
    with suppress(PlaywrightError):
        visible_inputs[min(len(code), len(visible_inputs)) - 1].press("Enter")


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

            phase = "поиск формы входа"
            login_input = page.get_by_test_id("auth_login_input")
            login_button = page.get_by_test_id("enter_with_sms_btn")
            if login_input.count() != 1 or login_button.count() != 1:
                raise SessionRecoveryError("Форма входа Profi.ru изменилась")

            phase = "отправка номера телефона"
            login_input.fill(settings.profi_login)
            login_button.click()

            # SMS часто приходит раньше, чем поле кода становится видимым.
            # Сначала открываем приём кода в Telegram, затем ждём интерфейс сайта.
            on_sms_requested()

            phase = "ожидание SMS-кода из Telegram"
            code = code_provider()
            if code == CANCEL_RECOVERY:
                raise SessionRecoveryError("Восстановление отменено пользователем")
            normalized_code = normalize_sms_code(code)
            if normalized_code is None:
                raise SessionRecoveryError("Получен некорректный SMS-код")

            phase = "ожидание поля SMS-кода на Profi.ru"
            otp_wait_ms = min(settings.page_timeout_ms, 30_000)
            otp_root = _wait_for_sms_code_root(
                page,
                settings.profi_otp_selector,
                otp_wait_ms,
            )
            if otp_root is None:
                # Иногда оболочка страницы загружается, а форма авторизации остаётся
                # на бесконечном индикаторе. Обновляем уже созданную попытку входа,
                # не нажимая кнопку запроса SMS повторно.
                phase = "повторная загрузка зависшей формы SMS-кода"
                page.reload(
                    wait_until="domcontentloaded",
                    timeout=settings.page_timeout_ms,
                )
                otp_root = _wait_for_sms_code_root(
                    page,
                    settings.profi_otp_selector,
                    otp_wait_ms,
                )
            if otp_root is None:
                raise SessionRecoveryError(
                    "Profi.ru не показал поле SMS-кода даже после обновления формы. "
                    "Чаще всего это означает, что через прокси не загрузился модуль авторизации."
                )

            phase = "ввод SMS-кода"
            _fill_sms_code(otp_root, settings.profi_otp_selector, normalized_code)

            phase = "проверка успешного входа"
            try:
                page.wait_for_selector(
                    settings.card_selector,
                    state="attached",
                    timeout=min(settings.page_timeout_ms, 20_000),
                )
            except PlaywrightTimeoutError:
                page.goto(
                    settings.page_url,
                    wait_until="domcontentloaded",
                    timeout=settings.page_timeout_ms,
                )
                page.wait_for_selector(
                    settings.card_selector,
                    state="attached",
                    timeout=settings.page_timeout_ms,
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
        self._on_success = on_success
        self.audience = audience or TelegramAudience(settings, log)
        self._last_started_at = 0.0
        if settings.auth_state_path.exists():
            self._session_ready.set()

    @property
    def in_progress(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _send(self, text: str) -> int:
        return await self.audience.send(self.bot, text)

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
            self._session_ready.clear()
            self._last_started_at = time.monotonic()
            self._task = asyncio.create_task(self._run(reason))
            return True

    async def _announce_sms_request(self) -> None:
        if not self._code_queue.empty():
            return
        self.awaiting_code = True
        await self._send(
            "📲 Запрос SMS отправлен в Profi.ru. Как только код придёт, "
            "отправьте боту только его цифры "
            f"в течение {self.settings.sms_code_timeout_sec // 60} мин.\n\n"
            "Для отмены используйте /cancel."
        )

    def _wait_for_code(self) -> str:
        try:
            return self._code_queue.get(timeout=self.settings.sms_code_timeout_sec)
        except Empty as exc:
            raise SessionRecoveryError("Время ожидания SMS-кода истекло") from exc

    async def _run(self, reason: str) -> None:
        if not self.audience.has_recipients:
            self.log.warning(
                "Ожидаю первого пользователя Telegram перед запросом SMS-кода"
            )
            await self.audience.wait_until_available()
        await self._send(
            "🔐 Сессия Profi.ru требует обновления.\n"
            f"Причина: {reason}\n\n"
            "Запрашиваю новый SMS-код автоматически…"
        )
        loop = asyncio.get_running_loop()

        def announce_from_thread() -> None:
            future = asyncio.run_coroutine_threadsafe(
                self._announce_sms_request(),
                loop,
            )
            future.result(timeout=30)

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
            await self._send(
                "❌ Не удалось обновить сессию Profi.ru.\n"
                f"Причина: {exc}\n\n"
                "Подробности сохранены в logs/debug/session_recovery_failed.txt и .html. "
                "Исправьте настройки при необходимости и отправьте /renew для повтора."
            )
        except Exception:
            self.log.exception("Непредвиденная ошибка восстановления сессии")
            await self._send(
                "❌ Внутренняя ошибка восстановления сессии. "
                "Отправьте /renew для повторной попытки."
            )
        finally:
            self.awaiting_code = False

    async def submit_code(self, raw_code: str) -> tuple[bool, str]:
        if not self.in_progress and not self.awaiting_code:
            return False, "Сейчас бот не ожидает SMS-код. Используйте /renew."

        code = normalize_sms_code(raw_code)
        if code is None:
            return False, "Код должен содержать от 4 до 8 цифр."

        try:
            self._code_queue.put_nowait(code)
        except Full:
            return False, "Код уже получен и обрабатывается."

        self.awaiting_code = False
        return True, "Код получен. Проверяю вход на Profi.ru…"

    async def cancel(self) -> bool:
        if not self.in_progress:
            return False
        with suppress(Full):
            self._code_queue.put_nowait(CANCEL_RECOVERY)
        self.awaiting_code = False
        return True

    async def wait_until_ready(self) -> None:
        await self._session_ready.wait()

    async def stop(self) -> None:
        await self.cancel()
        if self._task is not None:
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._task, timeout=10)
