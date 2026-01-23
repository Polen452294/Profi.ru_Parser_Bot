import json
import random
import time
from playwright.sync_api import sync_playwright
from logger_setup import setup_logger, log_json

from config import Settings
from auth import ensure_auth_state
from client import ProfiClient
from parser import parse_order_snippet
from storage import load_seen_ids, save_seen_ids, append_jsonl
from logger_setup import setup_logger, log_json

log = setup_logger("parser")

def sleep_human(base: int, jitter: int):
    time.sleep(base + random.uniform(0, jitter))


def sleep_backoff(attempt: int, min_sec: int = 180, max_sec: int = 900):
    t = min(max_sec, min_sec * (2 ** attempt))
    time.sleep(t + random.uniform(0, min(30, t * 0.1)))


def main():
    log = setup_logger("parser")
    s = Settings()

    log.info("Starting parser monitoring...")
    log.info("Settings: page_url=%s, poll_base=%s, poll_jitter=%s",
             getattr(s, "page_url", None), getattr(s, "poll_base_sec", None), getattr(s, "poll_jitter_sec", None))

    with sync_playwright() as p:
        ensure_auth_state(p, s)
        seen_ids = load_seen_ids(s.seen_ids_path)
        log.info("Loaded seen_ids: %d", len(seen_ids))

        with ProfiClient(p, s) as client:
            client.open_board()
            client.wait_cards()
            log.info("Board opened, cards are visible.")

            backoff_attempt = 0

            try:
                while True:
                    try:
                        client.soft_refresh()
                        client.wait_cards()

                        cards = client.cards_locator()
                        total = cards.count()
                        log.debug("Cards on page: %d", total)

                        new_orders = []
                        for i in range(total):
                            data = parse_order_snippet(cards.nth(i))
                            oid = data.get("order_id")
                            if not oid or oid in seen_ids:
                                continue
                            new_orders.append(data)
                            seen_ids.add(oid)

                        if new_orders:
                            append_jsonl(s.out_new_jsonl, new_orders)
                            save_seen_ids(s.seen_ids_path, seen_ids)

                            log.info("NEW orders found: %d", len(new_orders))
                            for o in new_orders:
                                log_json(log, "NEW_ORDER", o)

                        backoff_attempt = 0
                        sleep_human(s.poll_base_sec, s.poll_jitter_sec)

                    except Exception as e:
                        log.exception("Loop error: %s", e)
                        log.warning("Backoff to reduce load. attempt=%d", backoff_attempt)
                        sleep_backoff(backoff_attempt)
                        backoff_attempt = min(backoff_attempt + 1, 6)

            except KeyboardInterrupt:
                log.info("Stopped by user (Ctrl+C). Saving seen_ids and closing...")
                save_seen_ids(s.seen_ids_path, seen_ids)
                return


if __name__ == "__main__":
    main()
