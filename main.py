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

DEBUG_FILTER = False


def sleep_human(base: int, jitter: int):
    time.sleep(base + random.uniform(0, jitter))


def _get_poll_params(s: Settings):
    base = getattr(s, "poll_base_sec", getattr(s, "poll_base", 45))
    jitter = getattr(s, "poll_jitter_sec", getattr(s, "poll_jitter", 25))
    return int(base), int(jitter)


def _start_client(p, s: Settings) -> ProfiClient:
    client = ProfiClient(p, s).start()
    client.open_board()

    logger.info(
        "Page after open_board: title=%r url=%s",
        client.page.title(),
        client.page.url,
    )
    return client


def _restart_client(client: ProfiClient | None, p, s: Settings, reason: str) -> ProfiClient:
    logger.warning("Restarting Playwright client. reason=%s", reason)

    if client is not None:
        try:
            client.close()
        except Exception:
            logger.exception("Failed to close client during restart")

    time.sleep(5)
    new_client = _start_client(p, s)

    if not new_client.wait_cards():
        logger.warning("No cards right after client restart")

    return new_client


def main():
    s = Settings()

    with sync_playwright() as p:
        ensure_auth_state(p, s)

        seen_ids = load_seen_ids(s.seen_ids_path)
        poll_base, poll_jitter = _get_poll_params(s)

        logger.info("Starting parser monitoring...")
        logger.info(
            "Settings: page_url=%s, poll_base=%s, poll_jitter=%s",
            s.page_url, poll_base, poll_jitter,
        )
        logger.info("Loaded seen_ids: %d", len(seen_ids))

        client: ProfiClient | None = None
        net_errors = 0

        try:
            client = _start_client(p, s)

            if not client.wait_cards():
                logger.warning("No cards on first load. Will keep trying...")

            while True:
                try:
                    client.soft_refresh()
                    net_errors = 0

                except RuntimeError as e:
                    if str(e) == "PAGE_OR_BROWSER_CRASHED":
                        logger.exception("Browser/page crashed during refresh")
                        client = _restart_client(client, p, s, "page crashed on refresh")
                        continue
                    raise

                except Exception as e:
                    msg = str(e).lower()

                    if "err_name_not_resolved" in msg or "err_internet_disconnected" in msg:
                        net_errors += 1
                        logger.warning("Network/DNS error #%d: %s", net_errors, e)

                        if net_errors >= 3:
                            client = _restart_client(client, p, s, "too many network errors")
                            net_errors = 0

                        time.sleep(20)
                        continue

                    logger.exception("Unexpected error in main loop (refresh). Restarting client.")
                    client = _restart_client(client, p, s, "unexpected refresh error")
                    continue

                try:
                    ok = client.wait_cards()
                except RuntimeError as e:
                    if str(e) == "PAGE_OR_BROWSER_CRASHED":
                        logger.exception("Browser/page crashed while waiting cards")
                        client = _restart_client(client, p, s, "page crashed while waiting cards")
                        continue
                    raise

                if not ok:
                    title = client.page.title()
                    url = client.page.url

                    if ("вход" in title.lower()) or ("login" in title.lower()):
                        logger.warning(
                            "Seems logged out (TITLE=%r, URL=%s). Re-authenticating...",
                            title, url,
                        )
                        ensure_auth_state(p, s)
                        client = _restart_client(client, p, s, "re-auth after logout")
                        sleep_human(5, 5)
                        continue

                    logger.warning(
                        "Cards not found within %sms. Re-opening board. URL=%s TITLE=%r",
                        s.selector_timeout_ms, url, title,
                    )

                    try:
                        client.open_board()
                    except RuntimeError as e:
                        if str(e) == "PAGE_OR_BROWSER_CRASHED":
                            logger.exception("Browser/page crashed while reopening board")
                            client = _restart_client(client, p, s, "page crashed while reopening board")
                            continue
                        raise
                    except Exception:
                        logger.exception("Failed to reopen board. Restarting client.")
                        client = _restart_client(client, p, s, "failed open_board after no cards")
                        continue

                    sleep_human(10, 10)
                    continue

                try:
                    cards = client.cards_locator()
                    card_count = cards.count()
                except Exception:
                    logger.exception("Failed to access cards locator. Restarting client.")
                    client = _restart_client(client, p, s, "cards locator failed")
                    continue

                new_orders = []

                for i in range(card_count):
                    try:
                        data = parse_order_snippet(cards.nth(i))
                    except Exception:
                        logger.exception("Failed to parse card #%d", i)
                        continue

                    oid = data.get("order_id")

                    if not oid:
                        continue
                    if oid in seen_ids:
                        continue

                    match = order_matches_filter(data)

                    if DEBUG_FILTER:
                        t = data.get("title", "")
                        d = data.get("description", "")
                        text = f"{t} {d}".lower()
                        logger.info(
                            "FILTER oid=%s match=%s | title=%r | desc_len=%d | text_has_бот=%s",
                            oid, match, t, len(d), ("бот" in text),
                        )

                    if not match:
                        continue

                    seen_ids.add(oid)
                    new_orders.append(data)

                if new_orders:
                    for order in new_orders:
                        append_jsonl(s.out_jsonl_path, order)

                    save_seen_ids(s.seen_ids_path, seen_ids)
                    logger.info(
                        "Saved %d new orders. seen_ids=%d",
                        len(new_orders), len(seen_ids),
                    )

                sleep_human(poll_base, poll_jitter)

        except KeyboardInterrupt:
            logger.info("Stopped by user.")

        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    logger.exception("Failed to close client in finally.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    main()