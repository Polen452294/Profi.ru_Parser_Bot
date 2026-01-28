import re
from typing import Dict, Any

# Ловим:
# бот, бота, ботов, боты, ботик
# чат-бот, чат бот, чатбот, чат-ботов
# + латиницу: bot, bots, chatbot
BOT_RE = re.compile(r"(?iu)(?:чат[\s-]?)*бот\w*|\bbot\w*")


def order_matches_filter(data: dict) -> bool:
    # собираем текст из всех возможных полей
    parts = []
    for key in ("title", "description", "name", "text", "snippet", "details"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)

    # если вдруг title/description нет — пробуем собрать всё строковое из data
    if not parts:
        for v in data.values():
            if isinstance(v, str) and v.strip():
                parts.append(v)

    text = " ".join(parts)
    return bool(BOT_RE.search(text))
