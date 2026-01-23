from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    page_url: str = "https://profi.ru/backoffice/"
    state_path: str = "storage_state.json"
    headless: bool = True
    selector_timeout_ms: int = 60_000
    card_selector: str = 'a[data-testid$="_order-snippet"]'

    seen_ids_path: str = "seen_ids.json"
    out_new_jsonl: str = "new_orders.jsonl"

    # человечный мониторинг
    poll_base_sec: int = 45        # базовая пауза
    poll_jitter_sec: int = 25      # + случайно 0..25 сек
    backoff_min_sec: int = 180     # при проблемах ждать минимум 3 минуты
    backoff_max_sec: int = 900     # максимум 15 минут
