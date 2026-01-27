import random
import time
import logging

from playwright.sync_api import sync_playwright

from config import Settings
from auth import ensure_auth_state
from client import ProfiClient
from parser import parse_order_snippet
from storage import load_seen_ids, save_seen_ids, append_jsonl
from filters import order_matches_filter


logger = logging.getLogger("parser")

# Включи True на пару минут, чтобы увидеть, почему фильтр не матчится.
DEBUG_FILTER = True


def sleep_human(base: int, jitter: int):
    time.sleep(base + random.uniform(0, jitter))


def main():
    s = Settings()

    with sync_playwright() as p:
        ensure_auth_state(p, s)

        seen_ids = load_seen_ids(s.seen_ids_path)
        logger.info("Starting parser monitoring...")
        logger.info(
            "Settings: page_url=%s, poll_base_sec=%s, poll_jitter_sec=%s",
            s.page_url, s.poll_base_sec, s.poll_jitter_sec
        )
        logger.info("Loaded seen_ids: %d", len(seen_ids))

        with ProfiClient(p, s) as client:
            client.open_board()

            # первая попытка дождаться карточек (не фатально)
            if not client.wait_cards():
                logger.warning("No cards on first load. Will keep trying...")

            while True:
                try:
                    client.soft_refresh()

                    ok = client.wait_cards()
                    if not ok:
                        logger.warning(
                            "Cards not found within %sms. Re-opening board. URL=%s TITLE=%r",
                            s.selector_timeout_ms,
                            client.page.url,
                            client.page.title(),
                        )
                        client.open_board()
                        sleep_human(10, 10)
                        continue

                    cards = client.cards_locator()

                    new_orders = []
                    for i in range(cards.count()):
                        data = parse_order_snippet(cards.nth(i))
                        oid = data.get("order_id")
                        if not oid:
                            continue

                        # уже видели — пропускаем
                        if oid in seen_ids:
                            continue

                        title = data.get("title", "")
                        desc = data.get("description", "")
                        text = f"{title} {desc}".lower()

                        # Диагностика: покажет, что реально парсится и почему фильтр не совпал
                        if DEBUG_FILTER:
                            logger.info(
                                "DBG id=%s | title=%r | desc_len=%d | has_бот=%s | has_bot=%s",
                                oid, title, len(desc), ("бот" in text), ("bot" in text)
                            )

                        # фильтр: пропускаем только “бот/bot”
                        match = order_matches_filter(data)
                        if DEBUG_FILTER:
                            logger.info("DBG filter_match=%s", match)

                        if not match:
                            # ВАЖНО: не добавляем в seen_ids, чтобы при смене фильтра
                            # заказ мог пройти в будущем
                            continue

                        # прошёл фильтр — теперь считаем “увиденным”
                        seen_ids.add(oid)
                        new_orders.append(data)

                    if new_orders:
                        for order in new_orders:
                            append_jsonl(s.out_jsonl_path, order)
                            # Если у тебя тут ещё отправка в Telegram — она должна быть здесь

                        save_seen_ids(s.seen_ids_path, seen_ids)
                        logger.info("Saved %d new orders. seen_ids=%d", len(new_orders), len(seen_ids))

                    sleep_human(s.poll_base_sec, s.poll_jitter_sec)

                except KeyboardInterrupt:
                    logger.info("Stopped by user.")
                    break

                except Exception:
                    logger.exception("Unexpected error in main loop. Continue after short sleep.")
                    sleep_human(5, 5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    main()
