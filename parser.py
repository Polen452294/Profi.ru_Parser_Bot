from __future__ import annotations

from typing import Any


def normalize(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.replace("\u202f", " ").replace("\xa0", " ").split())
    return normalized or None


def _get_text(locator) -> str | None:
    try:
        if locator.count() == 0:
            return None
        return normalize(locator.first.inner_text())
    except Exception:
        return None


def _get_attribute(locator, name: str) -> str | None:
    try:
        return normalize(locator.get_attribute(name))
    except Exception:
        return None


def parse_order_snippet(card_locator) -> dict[str, Any]:
    """Извлекает данные из одной карточки заказа Profi.ru."""
    data_testid = _get_attribute(card_locator, "data-testid") or ""
    order_id = (
        data_testid.split("_", maxsplit=1)[0]
        if "_" in data_testid
        else _get_attribute(card_locator, "id")
    )

    title = _get_attribute(card_locator, "aria-label") or _get_text(
        card_locator.locator("h3")
    )

    return {
        "order_id": normalize(order_id),
        "title": title,
        "href": _get_attribute(card_locator, "href"),
        "price": _get_text(card_locator.locator('span[aria-hidden="true"]')),
        "description": _get_text(card_locator.locator("p")),
        "location": _get_text(card_locator.locator('li[aria-label^="Дистанционно"]')),
        "preferred_time": _get_text(
            card_locator.locator('li[aria-label^="Удобное время"]')
        ),
        "client_name": _get_text(card_locator.locator("div:has(svg) span").nth(0)),
        "posted_ago": _get_text(card_locator.locator('span:has-text("назад")').first),
    }
