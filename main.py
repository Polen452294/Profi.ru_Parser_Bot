import json
import time
import random
from playwright.sync_api import sync_playwright

from config import Settings
from auth import ensure_auth_state
from client import ProfiClient
from parser import parse_order_snippet
from storage import load_seen_ids, save_seen_ids, append_jsonl


def main():
    s = Settings(
        page_url="https://profi.ru/backoffice/",
        poll_interval_sec=10,
    )

    with sync_playwright() as p:
        ensure_auth_state(p, s)

        seen_ids = load_seen_ids(s.seen_ids_path)

        with ProfiClient(p, s) as client:
            client.open_board()
            client.wait_cards()

            print("▶ Мониторинг запущен. Ожидание новых заказов...\n")

            while True:
                # 1) обновляем страницу, чтобы React подтянул новые данные
                client.page.reload(wait_until="domcontentloaded", timeout=s.selector_timeout_ms)

                cards = client.cards_locator()
                new_orders = []

                for i in range(cards.count()):
                    card = cards.nth(i)
                    data = parse_order_snippet(card)

                    oid = data.get("order_id")
                    if not oid or oid in seen_ids:
                        continue

                    new_orders.append(data)
                    seen_ids.add(oid)

                # 2) если появились новые заказы
                if new_orders:
                    append_jsonl(s.out_new_jsonl, new_orders)
                    save_seen_ids(s.seen_ids_path, seen_ids)

                    print(f"🆕 Найдено новых заказов: {len(new_orders)}")
                    print(json.dumps(new_orders, ensure_ascii=False, indent=2))

                # 3) пауза
                base = 25   # базовая пауза
                jitter = 10 # + случайно 0..10 сек
                time.sleep(base + random.uniform(0, jitter))



if __name__ == "__main__":
    main()