import html
import re
from html import escape as h

def h(x):
    return html.escape(str(x)) if x else ""

def add_space_after_do(text: str) -> str:
    return re.sub(r'до(?!\s)', 'до ', text)

def format_order(o: dict) -> str:
    lines = [f"🧾 <b>Название:</b> {h(o['title'])}"]

    if o.get("price"):
        lines.append(add_space_after_do(f"💰 <b>Бюджет:</b> {h(o['price'])}"))
    if o.get("description"):
        text = o["description"]
        if len(text) > 3000:
            text = text[:3000] + "…"
        lines.append("\n📝 <b>Описание:</b>")
        lines.append(h(text))

    if o.get("href"):
        url = o["href"]
        if url.startswith("/"):
            url = "https://profi.ru" + url
        lines.append(f"🔗 <b>Ссылка:</b> {h(url)}")

    if o.get("order_id"):
        lines.append(f"🆔 <b>ID:</b> <code>{h(o['order_id'])}</code>")
    if o.get("preferred_time"):
        lines.append(f"🗓 <b>Когда удобно:</b> {h(o['preferred_time'])}")
    if o.get("posted_ago"):
        lines.append(f"⏱ <b>Опубликовано:</b> {h(o['posted_ago'])}")

    return "\n".join(lines)
