from typing import Optional


def norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return " ".join(s.replace("\u202f", " ").replace("\xa0", " ").split()).strip()


def get_text(locator) -> Optional[str]:
    try:
        if locator.count() == 0:
            return None
        return norm(locator.first.inner_text())
    except Exception:
        return None


def parse_order_snippet(card_locator) -> dict:
    """
    Парсит карточку заказа из DOM.
    Основано на твоём outerHTML.
    """
    data_testid = card_locator.get_attribute("data-testid") or ""
    order_id = data_testid.split("_")[0] if "_" in data_testid else (card_locator.get_attribute("id") or None)

    title = card_locator.get_attribute("aria-label") or get_text(card_locator.locator("h3"))
    href = card_locator.get_attribute("href")

    # Цена
    price = get_text(card_locator.locator('span[aria-hidden="true"]'))

    # Описание
    description = get_text(card_locator.locator("p"))

    # Локация и удобное время — по aria-label
    location = get_text(card_locator.locator('li[aria-label^="Дистанционно"]'))
    preferred_time = get_text(card_locator.locator('li[aria-label^="Удобное время"]'))

    # какое то время назад 
    posted_ago = get_text(card_locator.locator('span:has-text("назад")').first)

    client_name = get_text(card_locator.locator('div:has(svg) span').nth(0))

    return {
        "order_id": norm(order_id),
        "title": norm(title),
        "href": href,
        "price": price,
        "description": description,
        "location": location,
        "preferred_time": preferred_time,
        "client_name": client_name,
        "posted_ago": posted_ago,
    }
