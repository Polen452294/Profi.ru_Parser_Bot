from __future__ import annotations

from typing import Any
import re


BOT_KEYWORDS = (
    "бот",
    "бота",
    "боты",
    "ботов",
    "чат-бот",
    "чат бот",
    "bot",
)

DEV_KEYWORDS = (
    "разработка",
    "разработать",
    "разработчик",
    "создать",
    "создание",
    "сделать",
    "написать",
    "настройка бота",
    "реализовать",
    "нужен бот",
    "требуется бот",
    "бот под ключ",
)

VK_PATTERNS = (
    re.compile(r"(?iu)\bvk\b"),
    re.compile(r"(?iu)\bвк\b"),
    re.compile(r"(?iu)\bvkontakte\b"),
    re.compile(r"(?iu)\bвконтакте\b"),
    re.compile(r"(?iu)\bvk\.com\b"),
)

MAX_PATTERNS = (
    re.compile(r"(?iu)\bmax\b"),
    re.compile(r"(?iu)\bмакс\b"),
)

DISALLOWED_TOPICS = (
    "таргет",
    "таргетинг",
    "таргетированная реклама",
    "реклама",
    "маркетинг",
    "маркетолог",
    "лидогенерация",
    "лиды",
    "трафик",
    "контекстная реклама",
    "директ",
    "smm",
    "смм",
    "продвижение",
    "рекламная кампания",
    "специалист по рекламе",
    "настройка рекламы",
    "ведение рекламы",
)

DISALLOWED_PLATFORM_PATTERNS = (
    re.compile(r"(?iu)\btelegram\b"),
    re.compile(r"(?iu)\bтелеграм\b"),
    re.compile(r"(?iu)\bтг\b"),
    re.compile(r"(?iu)\btg\b"),
    re.compile(r"(?iu)\binstagram\b"),
    re.compile(r"(?iu)\bинстаграм\b"),
    re.compile(r"(?iu)\binsta\b"),
    re.compile(r"(?iu)\bwhatsapp\b"),
    re.compile(r"(?iu)\bватсап\b"),
    re.compile(r"(?iu)\bfacebook\b"),
    re.compile(r"(?iu)\bdiscord\b"),
)

BUDGET_PATTERNS = (
    re.compile(r"(?iu)(?:бюджет|budget|стоимость|цена|price)\s*[:\-]?\s*(?:от|до)?\s*(\d[\d\s]{0,12})"),
    re.compile(r"(?iu)(\d[\d\s]{3,12})\s*(?:₽|руб\.?|р\b|rub\b)"),
)


def _to_text(data: Any) -> str:
    if data is None:
        return ""

    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        parts: list[str] = []

        for key in (
            "title",
            "text",
            "description",
            "details",
            "snippet",
            "category",
            "budget",
            "price",
            "amount",
        ):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, (int, float)):
                parts.append(str(value))

        if not parts:
            for value in data.values():
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                elif isinstance(value, (int, float)):
                    parts.append(str(value))

        return "\n".join(parts)

    if isinstance(data, (list, tuple, set)):
        return "\n".join(_to_text(x) for x in data)

    return str(data)


def _normalize_text(text: str) -> str:
    text = (text or "").lower().replace("ё", "е").replace("\xa0", " ")
    return " ".join(text.split())


def _contains_bot_keyword(text: str) -> bool:
    return any(keyword in text for keyword in BOT_KEYWORDS)


def _contains_dev_intent(text: str) -> bool:
    return any(keyword in text for keyword in DEV_KEYWORDS)


def _contains_vk_or_max(text: str) -> bool:
    for rx in VK_PATTERNS:
        if rx.search(text):
            return True
    for rx in MAX_PATTERNS:
        if rx.search(text):
            return True
    return False


def _contains_disallowed_topics(text: str) -> bool:
    return any(keyword in text for keyword in DISALLOWED_TOPICS)


def _contains_disallowed_platforms(text: str) -> bool:
    for rx in DISALLOWED_PLATFORM_PATTERNS:
        if rx.search(text):
            return True
    return False


def _extract_budget_value(text: str) -> int | None:
    for rx in BUDGET_PATTERNS:
        match = rx.search(text)
        if not match:
            continue

        raw_value = match.group(1)
        digits = re.sub(r"[^\d]", "", raw_value)
        if not digits:
            continue

        try:
            value = int(digits)
        except ValueError:
            continue

        if value > 0:
            return value

    return None


def _budget_matches(text: str) -> bool:
    budget = _extract_budget_value(text)
    if budget is None:
        return True
    return budget >= 10000


def order_matches_filter(data: Any) -> bool:
    text = _normalize_text(_to_text(data))

    if not text:
        return False

    if _contains_disallowed_topics(text):
        return False

    if _contains_disallowed_platforms(text):
        return False

    if not _contains_vk_or_max(text):
        return False

    if not _contains_bot_keyword(text):
        return False

    if not _contains_dev_intent(text):
        return False

    if not _budget_matches(text):
        return False

    return True