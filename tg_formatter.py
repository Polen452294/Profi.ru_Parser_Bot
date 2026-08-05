from __future__ import annotations

from html import escape
import re
from typing import Any


MAX_DESCRIPTION_LENGTH = 2_800


def _html(value: Any) -> str:
    return escape(str(value), quote=True) if value not in (None, "") else ""


def _normalize_price(value: str) -> str:
    return re.sub(r"\bдо(?=\d)", "до ", value, flags=re.IGNORECASE)


def format_order(order: dict[str, Any]) -> str:
    title = _html(order.get("title") or "Без названия")
    lines = [f"🧾 <b>Заказ:</b> {title}"]

    if price := order.get("price"):
        lines.append(f"💰 <b>Бюджет:</b> {_html(_normalize_price(str(price)))}")

    if description := order.get("description"):
        description = str(description)
        if len(description) > MAX_DESCRIPTION_LENGTH:
            description = description[:MAX_DESCRIPTION_LENGTH].rstrip() + "…"
        lines.extend(("", "📝 <b>Описание:</b>", _html(description)))

    if location := order.get("location"):
        lines.append(f"📍 <b>Место:</b> {_html(location)}")
    if preferred_time := order.get("preferred_time"):
        lines.append(f"🗓 <b>Когда удобно:</b> {_html(preferred_time)}")
    if posted_ago := order.get("posted_ago"):
        lines.append(f"⏱ <b>Опубликовано:</b> {_html(posted_ago)}")

    if href := order.get("href"):
        url = str(href)
        if url.startswith("/"):
            url = "https://profi.ru" + url
        lines.append(f'🔗 <a href="{_html(url)}">Открыть заказ на Profi.ru</a>')

    if order_id := order.get("order_id"):
        lines.append(f"🆔 <b>ID:</b> <code>{_html(order_id)}</code>")

    return "\n".join(lines)
