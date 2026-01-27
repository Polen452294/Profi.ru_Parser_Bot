import re

# Ловит:
# - "бот", "бота", "ботов", "телеграм-бот", "telegram-бот"
# - "bot", "bots", "telegram bot", "tg bot"
RE_BOT = re.compile(r"(?:\bбот\w*\b|\bbot\w*\b)", re.IGNORECASE)

def order_matches_filter(order: dict) -> bool:
    parts = [
        order.get("title", ""),
        order.get("description", ""),
        order.get("client_name", ""),
        order.get("location", ""),
    ]
    text = " ".join(str(x) for x in parts if x)
    return bool(RE_BOT.search(text))
