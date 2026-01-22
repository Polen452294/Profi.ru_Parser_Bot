from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # куда заходим
    page_url: str = "https://profi.ru/backoffice/"

    # сессия
    state_path: str = "storage_state.json"

    # headless режим после первого логина
    headless: bool = True

    # ожидания
    selector_timeout_ms: int = 60_000

    # проверять каждые 10 секунд
    poll_interval_sec: int = 10 
    # селекторы
    card_selector: str = 'a[data-testid$="_order-snippet"]'

    # файлы для “новых заказов”
    seen_ids_path: str = "seen_ids.json"
    out_new_jsonl: str = "new_orders.jsonl"
