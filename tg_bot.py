import asyncio
from asyncio.log import logger
import os
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.enums import ParseMode

from tg_watcher import read_new_orders
from tg_formatter import format_order
from logger_setup import setup_logger, log_json

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
POLL_SEC = 3


async def main():
    log = setup_logger("bot")
    log.info("Bot started. poll=%ss", POLL_SEC)

    if not BOT_TOKEN:
        log.error("BOT_TOKEN is missing")
        return
    if ADMIN_CHAT_ID == 0:
        log.error("ADMIN_CHAT_ID is missing/invalid")
        return

    bot = Bot(token=BOT_TOKEN)

    try:
        while True:
            try:
                orders, _ = read_new_orders()
                if orders:
                    log.info("New orders detected in jsonl: %d", len(orders))

                for order in orders:
                    log_json(log, "SEND_ORDER", order)
                    if not order_matches_filter(order):
                        logger.info(
                            f"Order {order.get('order_id')} skipped by filter"
                        )
                        continue

                    text = order_matches_filter(order)
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )

                await asyncio.sleep(POLL_SEC)

            except Exception:
                log.exception("Bot loop error")
                await asyncio.sleep(5)

    except KeyboardInterrupt:
        log.info("Bot stopped by user.")

def order_matches_filter(order: dict) -> bool:
    keywords = ("бот",)

    text_parts = [
        order.get("title", ""),
        order.get("description", ""),
        order.get("client_name", ""),
        order.get("location", ""),
    ]

    full_text = " ".join(text_parts).lower()

    return any(keyword in full_text for keyword in keywords)

if __name__ == "__main__":
    asyncio.run(main())