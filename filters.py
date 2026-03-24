from __future__ import annotations

from typing import Any
import re


ALLOW_PHRASES = {
    "бот",
    "бота",
    "боты",
    "ботов",
    "ботик",
    "ботики",
    "чат-бот",
    "чат бот",
    "chat-bot",
    "chat bot",
    "vk bot",
    "vk-bot",
    "вк бот",
    "вк-бот",
    "max bot",
    "max-bot",
    "макс бот",
    "макс-бот",
}

ALLOWED_SUFFIXES = {
    "", "а", "ы", "у", "ом", "ов", "е", "ам", "ами", "ах",
    "ик", "ика", "ики", "иков", "ику", "иком", "иками",
}

FALSE_POSITIVE_TOKENS = {
    "работа", "работу", "работы", "работой", "работам", "работах",
    "доработка", "доработки", "доработку", "доработать", "доработаю",
    "разработка", "разработки", "разработку", "разрабатывать",
    "разработчик", "разработчика", "разработчики", "разработчиков",
    "работчик", "работчика", "работчики", "работчиков",
    "подработка", "подработки", "подработку", "подработать",
    "переработка", "переработки", "переработку",
}

FALSE_POSITIVE_CONTAINS = (
    "ботан",
    "ботокс",
    "ботва",
)

VK_PATTERNS = (
    re.compile(r"(?iu)\bvk\b"),
    re.compile(r"(?iu)\bвк\b"),
    re.compile(r"(?iu)\bvkontakte\b"),
    re.compile(r"(?iu)\bвконтакте\b"),
    re.compile(r"(?iu)\bvk\.com\b"),
    re.compile(r"(?iu)\bvkontakte\.ru\b"),
)

MAX_PATTERNS = (
    re.compile(r"(?iu)\bmax\b"),
    re.compile(r"(?iu)\bмакс\b"),
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
    re.compile(r"(?iu)\bwhats app\b"),
    re.compile(r"(?iu)\bfacebook\b"),
    re.compile(r"(?iu)\bdiscord\b"),
)

BUDGET_PATTERNS = (
    re.compile(r"(?iu)(?:бюджет|budget|стоимость|цена|price)\s*[:\-]?\s*(?:от\s*)?(\d[\d\s]{0,12})"),
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
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
            elif isinstance(v, (int, float)):
                parts.append(str(v))

        if not parts:
            for v in data.values():
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
                elif isinstance(v, (int, float)):
                    parts.append(str(v))

        return "\n".join(parts)

    if isinstance(data, (list, tuple, set)):
        return "\n".join(_to_text(x) for x in data)

    return str(data)


def _normalize_text(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = s.replace("\xa0", " ")
    return " ".join(s.split())


def _tokenize(s: str) -> list[str]:
    out_chars = []
    for ch in s:
        if ch.isalnum() or ch in "-_":
            out_chars.append(ch)
        else:
            out_chars.append(" ")
    return [t for t in "".join(out_chars).split() if t]


def _is_false_positive_token(tok: str) -> bool:
    if tok in FALSE_POSITIVE_TOKENS:
        return True

    for bad in FALSE_POSITIVE_CONTAINS:
        if bad in tok:
            return True

    if "работ" in tok or "разработ" in tok or "подработ" in tok or "переработ" in tok:
        return True

    return False


def _matches_bot_rule(tok: str) -> bool:
    if "бот" not in tok:
        return False

    if _is_false_positive_token(tok):
        return False

    idx = tok.find("бот")
    if idx == -1:
        return False

    prefix = tok[:idx]
    suffix = tok[idx + 3:]

    if len(prefix) > 4:
        return False

    if suffix in ALLOWED_SUFFIXES:
        return True

    for suf in sorted(ALLOWED_SUFFIXES, key=len, reverse=True):
        if suf and suffix.startswith(suf):
            return True

    return False


def _contains_bot_request(text: str) -> bool:
    for phrase in ALLOW_PHRASES:
        if phrase in text:
            return True

    for tok in _tokenize(text):
        if _matches_bot_rule(tok):
            return True

    return False


def _contains_vk_or_max(text: str) -> bool:
    for rx in VK_PATTERNS:
        if rx.search(text):
            return True

    for rx in MAX_PATTERNS:
        if rx.search(text):
            return True

    return False


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
    return budget > 10000


def order_matches_filter(data: Any) -> bool:
    text = _normalize_text(_to_text(data))

    if not text:
        return False

    if not _contains_bot_request(text):
        return False

    if not _contains_vk_or_max(text):
        return False

    if _contains_disallowed_platforms(text):
        return False

    if not _budget_matches(text):
        return False

    return True