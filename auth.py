import os
from playwright.sync_api import Playwright

from config import Settings


def ensure_auth_state(p: Playwright, s: Settings) -> None:
    if os.path.exists(s.state_path):
        return

    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto(s.page_url, wait_until="domcontentloaded")

    print("\n=== ПЕРВЫЙ ЗАПУСК: НУЖНА АВТОРИЗАЦИЯ ===")
    print("1) Войди в аккаунт вручную.")
    print("2) Перейди на страницу со списком заказов (где видны карточки).")
    print("3) Затем вернись в консоль и нажми Enter.\n")
    input("Нажми Enter, когда будешь готов... ")

    context.storage_state(path=s.state_path)
    browser.close()
    print(f"OK: сохранено {s.state_path}\n")
