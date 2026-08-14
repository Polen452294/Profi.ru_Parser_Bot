from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


TELEGRAM_BOT_PATTERNS = (
    re.compile(
        r"(?iu)(?:"
        r"\b(?:телеграм\w*|telegram|тг)\b.*?\b(?:бот\w*|bots?)\b|"
        r"\b(?:бот\w*|bots?)\b.*?\b(?:телеграм\w*|telegram|тг)\b"
        r")"
    ),
)

MAX_BOT_PATTERNS = (
    re.compile(
        r"(?iu)(?:"
        r"\b(?:макс|max)\b.*?\b(?:бот\w*|bots?)\b|"
        r"\b(?:бот\w*|bots?)\b.*?\b(?:макс|max)\b"
        r")"
    ),
)

CRM_PATTERNS = (
    re.compile(r"(?iu)\bcrm\b"),
    re.compile(r"(?iu)\bcrm[- ]?систем\w*\b"),
    re.compile(r"(?iu)\bцрм\b"),
    re.compile(r"(?iu)\bцрм[- ]?систем\w*\b"),
    re.compile(r"(?iu)\bсрм\b"),
    re.compile(r"(?iu)\bсрм[- ]?систем\w*\b"),
    re.compile(r"(?iu)\bси[- ]?ар[- ]?эм\b"),
)

PARSER_PATTERNS = (
    re.compile(r"(?iu)\bпарсер\w*\b"),
    re.compile(r"(?iu)\bпарсинг\w*\b"),
    re.compile(r"(?iu)\bпарсить\w*\b"),
    re.compile(r"(?iu)\bспарс\w*\b"),
    re.compile(r"(?iu)\bраспарс\w*\b"),
    re.compile(r"(?iu)\bparser\w*\b"),
    re.compile(r"(?iu)\bparsing\b"),
    re.compile(r"(?iu)\bparse\b"),
)

TARGET_PATTERN_GROUPS = (
    ("Telegram-боты", TELEGRAM_BOT_PATTERNS),
    ("MAX-боты", MAX_BOT_PATTERNS),
    ("Парсеры и парсинг", PARSER_PATTERNS),
    ("Разработка CRM", CRM_PATTERNS),
)

TARGET_KEYWORD_PATTERNS = tuple(
    pattern
    for _group, patterns in TARGET_PATTERN_GROUPS
    for pattern in patterns
)

MIN_BUDGET_RUB = 5_000

DEV_KEYWORDS = (
    "разработка",
    "разработать",
    "разработчик",
    "создать",
    "создание",
    "сделать",
    "написать",
    "реализовать",
    "доработать",
    "настроить",
    "настройка",
    "внедрить",
    "внедрение",
    "интегрировать",
    "интеграция",
    "нужен",
    "нужна",
    "нужно",
    "нужны",
    "требуется",
    "требуются",
    "необходимо",
    "надо",
    "ищу",
    "заказать",
    "хочу",
)

DISALLOWED_TOPICS = (
    "таргет",
    "таргетинг",
    "таргетированная реклама",
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
                normalized_value = value.strip()
                if key in {"budget", "price", "amount"}:
                    parts.append(f"{key}: {normalized_value}")
                else:
                    parts.append(normalized_value)
            elif isinstance(value, (int, float)):
                if key in {"budget", "price", "amount"}:
                    parts.append(f"{key}: {value}")
                else:
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


def _find_target_match(text: str) -> tuple[re.Match[str], str] | None:
    for group, patterns in TARGET_PATTERN_GROUPS:
        for pattern in patterns:
            match = pattern.search(text)
            if match is not None:
                return match, group
    return None


def _contains_dev_intent(text: str) -> bool:
    return any(keyword in text for keyword in DEV_KEYWORDS)


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
    return budget >= MIN_BUDGET_RUB


def order_matches_filter(data: Any) -> bool:
    text = _normalize_text(_to_text(data))

    if not text:
        return False

    if _find_target_match(text) is None:
        return False

    if not _contains_dev_intent(text):
        return False

    if _contains_disallowed_topics(text):
        return False

    if _contains_disallowed_platforms(text):
        return False

    if not _budget_matches(text):
        return False

    return True


@dataclass(frozen=True)
class FilterRule:
    phrase: str
    group: str


@dataclass(frozen=True)
class FilterDecision:
    accepted: bool
    matched_rule: FilterRule | None = None
    excluded_rule: FilterRule | None = None


def evaluate_order(data: Any) -> FilterDecision:
    """Объясняет решение фильтра, не изменяя исходные правила отбора."""
    text = _normalize_text(_to_text(data))
    if not text:
        return FilterDecision(accepted=False)

    target = _find_target_match(text)
    if target is None:
        return FilterDecision(accepted=False)

    target_match, target_group = target
    matched_rule = FilterRule(target_match.group(0), target_group)
    if not _contains_dev_intent(text):
        return FilterDecision(
            accepted=False,
            matched_rule=matched_rule,
            excluded_rule=FilterRule(
                "нет запроса на разработку или внедрение",
                "Контекст заявки",
            ),
        )

    disallowed_topic = next(
        (keyword for keyword in DISALLOWED_TOPICS if keyword in text),
        None,
    )
    if disallowed_topic:
        return FilterDecision(
            accepted=False,
            matched_rule=matched_rule,
            excluded_rule=FilterRule(disallowed_topic, "Исключённая тематика"),
        )

    platform_match = next(
        (
            match
            for pattern in DISALLOWED_PLATFORM_PATTERNS
            if (match := pattern.search(text))
        ),
        None,
    )
    if platform_match:
        return FilterDecision(
            accepted=False,
            matched_rule=matched_rule,
            excluded_rule=FilterRule(
                platform_match.group(0),
                "Исключённая платформа",
            ),
        )

    if not _budget_matches(text):
        return FilterDecision(
            accepted=False,
            matched_rule=matched_rule,
            excluded_rule=FilterRule(
                f"цена ниже {MIN_BUDGET_RUB:,} руб.".replace(",", " "),
                "Бюджет",
            ),
        )

    return FilterDecision(accepted=True, matched_rule=matched_rule)
