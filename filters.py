from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


TARGET_KEYWORD_PATTERNS = (
    re.compile(r"(?iu)\bбот\b"),
    re.compile(r"(?iu)\bбота\b"),
    re.compile(r"(?iu)\bботы\b"),
    re.compile(r"(?iu)\bботов\b"),
    re.compile(r"(?iu)\bботом\b"),
    re.compile(r"(?iu)\bботу\b"),
    re.compile(r"(?iu)\bчат[- ]?бот\b"),
    re.compile(r"(?iu)\bчат[- ]?бота\b"),
    re.compile(r"(?iu)\bchat[- ]?bot\b"),
    re.compile(r"(?iu)\bbot\b"),
    re.compile(r"(?iu)\bbots\b"),

    re.compile(r"(?iu)\bcrm\b"),
    re.compile(r"(?iu)\bcrm[- ]?систем\w*\b"),
    re.compile(r"(?iu)\bцрм\b"),
    re.compile(r"(?iu)\bцрм[- ]?систем\w*\b"),
    re.compile(r"(?iu)\bсрм\b"),
    re.compile(r"(?iu)\bсрм[- ]?систем\w*\b"),
    re.compile(r"(?iu)\bси[- ]?ар[- ]?эм\b"),

    re.compile(r"(?iu)\bпарсер\w*\b"),
    re.compile(r"(?iu)\bпарсинг\w*\b"),
    re.compile(r"(?iu)\bпарсить\b"),
    re.compile(r"(?iu)\bпарсить\w*\b"),
    re.compile(r"(?iu)\bспарс\w*\b"),
    re.compile(r"(?iu)\bраспарс\w*\b"),
    re.compile(r"(?iu)\bparser\w*\b"),
    re.compile(r"(?iu)\bparsing\b"),
    re.compile(r"(?iu)\bparse\b"),

    re.compile(r"(?iu)\bавтоматизац\w*\b"),
    re.compile(r"(?iu)\bавтоматизир\w*\b"),
    re.compile(r"(?iu)\bавтоматическ\w*\b"),
    re.compile(r"(?iu)\bautomation\b"),
    re.compile(r"(?iu)\bautomate\b"),
    re.compile(r"(?iu)\bautomated\b"),
)

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


def _contains_target_keyword(text: str) -> bool:
    for rx in TARGET_KEYWORD_PATTERNS:
        if rx.search(text):
            return True
    return False


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
    return budget >= 10000


def order_matches_filter(data: Any) -> bool:
    text = _normalize_text(_to_text(data))

    if not text:
        return False

    if not _contains_target_keyword(text):
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

    target_match = next(
        (match for pattern in TARGET_KEYWORD_PATTERNS if (match := pattern.search(text))),
        None,
    )
    if target_match is None:
        return FilterDecision(accepted=False)

    matched_rule = FilterRule(target_match.group(0), "Целевая тематика")
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
            excluded_rule=FilterRule("бюджет ниже 10 000 ₽", "Бюджет"),
        )

    return FilterDecision(accepted=True, matched_rule=matched_rule)
