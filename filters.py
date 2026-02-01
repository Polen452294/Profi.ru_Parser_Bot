from __future__ import annotations
from typing import Any


# === Настройки фильтра ===

# Явные фразы, которые должны проходить, даже если правило "<=4 символа перед 'бот'"
# не сработало (например, "телеграм-бот" -> "телеграм-" длиннее 4).
ALLOW_PHRASES = {
    "телеграм-бот",
    "телеграм бот",
    "telegram-bot",
    "telegram bot",
    "tg-bot",
    "tg bot",
    "тг-бот",
}

# Суффиксы после "бот", которые считаем нормальными окончаниями слова "бот".
# (чтобы "бота/боты/ботов/ботом/ботами/ботик/ботики..." проходили)
ALLOWED_SUFFIXES = {
    "", "а", "ы", "у", "ом", "ов", "е", "ам", "ами", "ах",
    "ик", "ика", "ики", "иков", "ику", "иком", "иками",
}

# Слова, в которых "бот" встречается как часть других слов (ложные срабатывания),
# или домены типа "работчик", "разработчик", "подработка" и т.д.
# Важно: мы НЕ "баним заказ", если эти слова встречаются рядом.
# Мы просто не считаем такие токены "валидным попаданием по боту".
FALSE_POSITIVE_TOKENS = {
    "работа", "работу", "работы", "работой", "работам", "работах",
    "доработка", "доработки", "доработку", "доработать", "доработаю",
    "разработка", "разработки", "разработку", "разрабатывать",
    "разработчик", "разработчика", "разработчики", "разработчиков",
    "работчик", "работчика", "работчики", "работчиков",
    "подработка", "подработки", "подработку", "подработать",
    "переработка", "переработки", "переработку",
}

# Ещё несколько частых "не бот" слов, которые содержат "бот" в середине
# и могут совпасть по простому правилу.
# (можешь дополнять по логам)
FALSE_POSITIVE_CONTAINS = (
    "ботан",   # ботан, ботаны, ботаник...
    "ботокс",  # ботокс
    "ботва",   # ботва
)


def _to_text(data: Any) -> str:
    """
    Превращает вход (str / dict / list / etc.) в единый текст для фильтрации.
    """
    if data is None:
        return ""

    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        # Склеим самые вероятные поля
        parts: list[str] = []
        for key in ("title", "text", "description", "details", "snippet", "category"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())

        # Если ничего не нашли — соберём любые строковые значения
        if not parts:
            for v in data.values():
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())

        return "\n".join(parts)

    if isinstance(data, (list, tuple, set)):
        return "\n".join(_to_text(x) for x in data)

    return str(data)


def _normalize_text(s: str) -> str:
    """
    Нормализация текста: lower, замена 'ё' -> 'е', выравнивание пробелов.
    """
    s = (s or "").lower().replace("ё", "е")
    return " ".join(s.split())


def _tokenize(s: str) -> list[str]:
    """
    Простая токенизация: оставляем буквы/цифры/дефис, остальное -> разделитель.
    """
    out_chars = []
    for ch in s:
        if ch.isalnum() or ch in "-_":
            out_chars.append(ch)
        else:
            out_chars.append(" ")
    return [t for t in "".join(out_chars).split() if t]


def _is_false_positive_token(tok: str) -> bool:
    """
    Отсекаем токены, которые выглядят как 'работчик/разработчик' и т.п.
    """
    if tok in FALSE_POSITIVE_TOKENS:
        return True

    for bad in FALSE_POSITIVE_CONTAINS:
        if bad in tok:
            return True

    # Частый кейс: слова с корнем "работ" дают ложные совпадения через "...бот..."
    # Например: "работчик" -> "ра" + "бот" (<=4) и может пройти без доп. отсечки.
    if "работ" in tok or "разработ" in tok or "подработ" in tok or "переработ" in tok:
        return True

    return False


def _matches_bot_rule(tok: str) -> bool:
    """
    True, если токен считается "бот"-словом по правилам:
    - содержит "бот"
    - перед "бот" в этом токене <= 4 символов (лат/кирил/цифры/дефис/подчёрк)
    - суффикс после "бот" похож на окончания слова "бот"
    - токен не является ложным совпадением (работчик/разработчик и т.п.)
    """
    if "бот" not in tok:
        return False

    if _is_false_positive_token(tok):
        return False

    idx = tok.find("бот")
    if idx == -1:
        return False

    prefix = tok[:idx]
    suffix = tok[idx + 3 :]

    # Разрешаем только "короткий префикс" (<=4),
    # чтобы "чат-бот" проходил, а "разработчик/работчик" не проходил.
    # (исключения типа телеграм-бот обрабатываются отдельно через ALLOW_PHRASES)
    if len(prefix) > 4:
        return False

    # Проверка суффикса (окончания). Если суффикс длиннее, но начинается с одного
    # из разрешённых окончаний — тоже принимаем (например "ботики", "ботов", "ботом").
    # Здесь мы считаем окончания по первым 1-5 символам.
    if suffix in ALLOWED_SUFFIXES:
        return True

    # Иногда suffix может быть "ов..." и т.п. Проверим по началу.
    for suf in sorted(ALLOWED_SUFFIXES, key=len, reverse=True):
        if suf and suffix.startswith(suf):
            return True

    # Если после "бот" идёт что-то странное, не похоже на слово "бот" — не считаем.
    return False


def order_matches_filter(data: Any) -> bool:
    """
    Главная функция фильтра: принимает dict/str и возвращает True, если заказ подходит.
    """
    text = _normalize_text(_to_text(data))

    if not text:
        return False

    # 1) Быстрые "разрешающие" фразы (исключения)
    # Например "телеграм-бот" должен проходить, хотя "телеграм-" > 4.
    for phrase in ALLOW_PHRASES:
        if phrase in text:
            return True

    # 2) Токенизация и проверка по правилу "<=4 символа перед 'бот'"
    tokens = _tokenize(text)
    for tok in tokens:
        # Также допускаем вариант без дефисов/подчёркиваний в токене
        # (чатбот, телеграмбот — но телеграмбот всё равно попадёт через ALLOW_PHRASES)
        if _matches_bot_rule(tok):
            return True

    return False
