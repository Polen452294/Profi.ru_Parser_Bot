from dataclasses import dataclass

bot_cursor_path: str = "bot_cursor.json"

@dataclass(frozen=True)
class Settings:
    page_url: str = "https://profi.ru/backoffice/"
    state_path: str = "storage_state.json"
    auth_state_path: str = "storage_state.json"
    out_jsonl_path: str = "new_orders.jsonl"

    headless: bool = True
    selector_timeout_ms: int = 60_000
    card_selector: str = 'a[data-testid$="_order-snippet"]'

    seen_ids_path: str = "seen_ids.json"
    out_new_jsonl: str = "new_orders.jsonl"

    poll_base_sec: int = 45
    poll_jitter_sec: int = 25
    backoff_min_sec: int = 180
    backoff_max_sec: int = 900