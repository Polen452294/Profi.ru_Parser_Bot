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

# 🔧 Включай на короткое время для отладки фильтра
DEBUG_FILTER = False


def sleep_human(base: int, jitter: int):
    time.sleep(base + random.uniform(0, jitter))


def _get_poll_params(s: Settings):
    base = getattr(s, "poll_base_sec", getattr(s, "poll_base", 45))
    jitter = getattr(s, "poll_jitter_sec", getattr(s, "poll_jitter", 25))
    return int(base), int(jitter)


def main():
    s = Settings()

    with sync_playwright() as p:
        ensure_auth_state(p, s)

        seen_ids = load_seen_ids(s.seen_ids_path)
        poll_base, poll_jitter = _get_poll_params(s)

        logger.info("Starting parser monitoring...")
        logger.info(
            "Settings: page_url=%s, poll_base=%s, poll_jitter=%s",
            s.page_url, poll_base, poll_jitter
        )
        logger.info("Loaded seen_ids: %d", len(seen_ids))

        with ProfiClient(p, s) as client:
            client.open_board()

            logger.info(
                "Page after open_board: title=%r url=%s",
                client.page.title(),
                client.page.url
            )

            # первая попытка
            if not client.wait_cards():
                logger.warning("No cards on first load. Will keep trying...")

            while True:
                try:
                    client.soft_refresh()

                    ok = client.wait_cards()
                    if not ok:
                        title = client.page.title()
                        url = client.page.url

                        if ("вход" in title.lower()) or ("login" in title.lower()):
                            logger.warning(
                                "Seems logged out (TITLE=%r, URL=%s). Re-authenticating...",
                                title, url
                            )
                            ensure_auth_state(p, s)
                            client.open_board()
                            sleep_human(5, 5)
                            continue

                        logger.warning(
                            "Cards not found within %sms. Re-opening board. URL=%s TITLE=%r",
                            s.selector_timeout_ms, url, title
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
                        if oid in seen_ids:
                            continue

                        # 🧠 ФИЛЬТР
                        match = order_matches_filter(data)

                        if DEBUG_FILTER:
                            title = data.get("title", "")
                            desc = data.get("description", "")
                            text = f"{title} {desc}".lower()

                            match = order_matches_filter(data)

                            logger.info(
                                "FILTER oid=%s match=%s | title=%r | desc_len=%d | text_has_бот=%s",
                                oid, match, title, len(desc), ("бот" in text)
                            )

                        if not match:
                            continue  # ⛔ не бот → не пишем, не запоминаем

                        # ✅ только здесь считаем заказ обработанным
                        seen_ids.add(oid)
                        new_orders.append(data)

                    if new_orders:
                        for order in new_orders:
                            append_jsonl(s.out_jsonl_path, order)

                        save_seen_ids(s.seen_ids_path, seen_ids)
                        logger.info(
                            "Saved %d new orders. seen_ids=%d",
                            len(new_orders), len(seen_ids)
                        )

                    sleep_human(poll_base, poll_jitter)

                except KeyboardInterrupt:
                    logger.info("Stopped by user.")
                    break

                except Exception:
                    logger.exception("Unexpected error in main loop. Sleeping a bit and continuing.")
                    sleep_human(5, 5)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    main()
