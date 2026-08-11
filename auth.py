from __future__ import annotations

from playwright.sync_api import Playwright

from browser_identity import (
    load_browser_identity,
    resolve_http_impersonate,
    stealth_init_script,
)
from browser_sessions import (
    DEFAULT_PROFILE_NAME,
    BrowserSessionManager,
    BrowserStorageMode,
    build_profile_catalog,
    identity_launch_options,
)
from config import Settings


def authorize(playwright: Playwright, settings: Settings, *, force: bool = False) -> bool:
    """Открывает браузер для ручного входа и сохраняет сессию Profi.ru."""
    if settings.auth_state_path.exists() and not force:
        return False

    settings.ensure_directories()
    identity = load_browser_identity(
        profile_path=settings.profi_browser_profile_path,
        user_agent=settings.profi_user_agent,
        impersonate=resolve_http_impersonate(
            settings.profi_user_agent,
            settings.profi_http_impersonate,
        ),
        locale=settings.profi_browser_locale,
        timezone_id=settings.profi_browser_timezone,
    )
    profiles = build_profile_catalog(identity)
    profile = profiles.get(DEFAULT_PROFILE_NAME)
    browser = playwright.chromium.launch(
        **identity_launch_options(
            settings.playwright_launch_options(headless=False),
            profile,
            stealth=settings.profi_browser_stealth,
        )
    )

    try:
        manager = BrowserSessionManager(
            browser,
            profiles,
            auth_state_path=settings.auth_state_path,
            extra_http_headers=identity.http_headers,
            init_scripts=(stealth_init_script(identity),)
            if settings.profi_browser_stealth
            else (),
        )
        session = manager.create_session(
            profile.name,
            storage_mode=BrowserStorageMode.FRESH,
        )
        context = session.context
        page = session.page
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

        context.storage_state(
            path=str(settings.auth_state_path),
            indexed_db=True,
        )
        settings.auth_state_path.chmod(0o600)
        print(f"\nСессия сохранена: {settings.auth_state_path}\n")
        return True
    finally:
        if "session" in locals():
            session.close()
        browser.close()


def ensure_auth_state(playwright: Playwright, settings: Settings) -> None:
    authorize(playwright, settings, force=False)
