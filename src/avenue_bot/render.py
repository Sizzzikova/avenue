"""Сборка текста дайджеста для Telegram (parse_mode=HTML)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .config import Config
from .models import Car
from .state import Snapshot, mark

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
NBSP = " "


@dataclass
class Post:
    """Одно сообщение: подпись и фотографии к ней."""

    caption: str
    images: list[str] = field(default_factory=list)


def format_price(value: Optional[int]) -> str:
    """8400 -> '8 400 ₽' с неразрывными пробелами, чтобы цена не рвалась на строки."""
    if value is None:
        return ""
    return f"{value:,}".replace(",", NBSP) + NBSP + "₽"


def format_date(value: Optional[str]) -> str:
    """'2026-12-31' -> '31.12.2026'. Непонятный формат возвращаем как есть."""
    if not value:
        return ""
    parts = value.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return value


def car_line(car: Car, badge: str) -> str:
    name = f'<a href="{html.escape(car.url, quote=True)}">{html.escape(car.name)}</a>'
    prefix = f"{badge} " if badge else ""

    prices = []
    if car.old_price:
        prices.append(f"<s>{format_price(car.old_price)}</s>")
    if car.day_price:
        prices.append(f"<b>{format_price(car.day_price)}</b>/сут")
    price_text = " ".join(prices) or format_price(car.base_price)

    line = f"{prefix}{name} — {price_text}"
    if car.discount_pct:
        line += f" · −{car.discount_pct}%"
    if car.discount_to:
        line += f" (до {format_date(car.discount_to)})"
    return line


def caption_length(markup: str) -> int:
    """Длина подписи так, как её считает Telegram.

    Два расхождения с наивным len(): при parse_mode=HTML лимит применяется к
    видимому тексту без тегов, а длина меряется в кодовых единицах UTF-16 —
    поэтому эмодзи и большинство спецсимволов считаются за два.
    """
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return len(text.encode("utf-16-le")) // 2


def _compose(header: str, blocks: dict[str, list[str]], footer: str) -> str:
    chunks = [header]
    for city, lines in blocks.items():
        chunks.append("")
        chunks.append(f"🏙 <b>{html.escape(city)}</b>")
        chunks.extend(lines)
    if footer:
        chunks.append("")
        chunks.append(footer)
    return "\n".join(chunks)


def build_digest(
    cars: list[Car],
    previous: Snapshot,
    config: Config,
    today: Optional[date] = None,
) -> list[Post]:
    """Собрать дайджест, разбив его на сообщения под лимиты Telegram.

    Ограничения площадки: до `media_group_limit` фото в альбоме и
    `caption_limit` символов в подписи. Семь машин укладываются в одно
    сообщение, но при росте автопарка разбиение включится само.
    """
    if not cars:
        return []

    today = today or date.today()
    header = (
        f"🚗 <b>Автомобили со скидкой</b> ({today.day} {MONTHS_GENITIVE[today.month - 1]})"
    )
    footer = html.escape(config.footer) if config.footer else ""

    order = {city.name: index for index, city in enumerate(config.cities)}
    ordered = sorted(cars, key=lambda c: (order.get(c.city, 99), c.name))

    posts: list[Post] = []
    blocks: dict[str, list[str]] = {}
    images: list[str] = []

    def flush() -> None:
        if blocks:
            posts.append(Post(caption=_compose(header, blocks, footer), images=list(images)))
        blocks.clear()
        images.clear()

    for car in ordered:
        line = car_line(car, mark(previous, car))
        candidate = dict(blocks)
        candidate[car.city] = [*candidate.get(car.city, []), line]

        too_long = caption_length(_compose(header, candidate, footer)) > config.caption_limit
        too_many = car.image_url and len(images) >= config.media_group_limit
        if blocks and (too_long or too_many):
            flush()

        blocks.setdefault(car.city, []).append(line)
        if car.image_url:
            images.append(car.image_url)

    flush()
    return posts
