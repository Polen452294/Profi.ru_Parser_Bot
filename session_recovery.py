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
    pass


def normalize_sms_code(value: str) -> str | None:
    code = re.sub(r"[\s-]", "", value or "")
    return code if SMS_CODE_PATTERN.fullmatch(code) else None


def _save_recovery_debug(page, settings: Settings) -> Path:
    settings.debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = settings.debug_dir / "session_recovery_failed.png"
    with suppress(Exception):
        page.screenshot(path=str(screenshot_path), full_page=True)
    return screenshot_path


def _fill_sms_code(page, selector: str, code: str) -> None:
    inputs = page.locator(selector)
    count = inputs.count()
    visible_inputs = [inputs.nth(index) for index in range(count) if inputs.nth(index).is_visible()]

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

        try:
            page.goto(
                settings.page_url,
                wait_until="domcontentloaded",
                timeout=settings.page_timeout_ms,
            )

            login_input = page.get_by_test_id("auth_login_input")
            login_button = page.get_by_test_id("enter_with_sms_btn")
            if login_input.count() != 1 or login_button.count() != 1:
                raise SessionRecoveryError("Форма входа Profi.ru изменилась")

            login_input.fill(settings.profi_login)
            login_button.click()

            page.wait_for_selector(
                settings.profi_otp_selector,
                state="visible",
                timeout=settings.page_timeout_ms,
            )
            on_sms_requested()

            code = code_provider()
            if code == CANCEL_RECOVERY:
                raise SessionRecoveryError("Восстановление отменено пользователем")
            normalized_code = normalize_sms_code(code)
            if normalized_code is None:
                raise SessionRecoveryError("Получен некорректный SMS-код")

            _fill_sms_code(page, settings.profi_otp_selector, normalized_code)

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
            screenshot_path = _save_recovery_debug(page, settings)
            if isinstance(exc, SessionRecoveryError):
                raise
            raise SessionRecoveryError(
                f"Не удалось обновить сессию Profi.ru. Диагностика: {screenshot_path}"
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
        self.awaiting_code = True
        await self._send(
            "📲 Profi.ru отправил SMS-код. Отправьте боту только цифры кода "
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
            await self._send(
                "❌ Не удалось обновить сессию Profi.ru.\n"
                f"Причина: {exc}\n\n"
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
        if not self.awaiting_code:
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
