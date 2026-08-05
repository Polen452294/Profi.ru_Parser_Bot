from __future__ import annotations

from playwright.sync_api import Playwright

from config import Settings


def authorize(playwright: Playwright, settings: Settings, *, force: bool = False) -> bool:
    """Открывает браузер для ручного входа и сохраняет сессию Profi.ru."""
    if settings.auth_state_path.exists() and not force:
        return False

    settings.ensure_directories()
    browser = playwright.chromium.launch(headless=False)

    try:
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(
            settings.page_url,
            wait_until="domcontentloaded",
            timeout=settings.page_timeout_ms,
        )

        print("\n=== АВТОРИЗАЦИЯ НА PROFI.RU ===")
        print("1. В открывшемся браузере войдите в аккаунт.")
        print("2. Откройте страницу со списком заказов.")
        print("3. Убедитесь, что карточки заказов появились.")
        input("4. Вернитесь сюда и нажмите Enter... ")

        context.storage_state(path=str(settings.auth_state_path))
        settings.auth_state_path.chmod(0o600)
        print(f"\nСессия сохранена: {settings.auth_state_path}\n")
        return True
    finally:
        browser.close()


def ensure_auth_state(playwright: Playwright, settings: Settings) -> None:
    authorize(playwright, settings, force=False)
