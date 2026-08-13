"""Разбор страницы /aktcii/ и добор цен через AJAX-эндпоинт сайта.

Почему два шага. В HTML лежат только карточки машин и базовая цена
(`static-price`). Итоговую цену со скидкой и процент рисует JavaScript,
дёргая POST /ajax/calc_catalog.php. Мы делаем ровно тот же запрос —
это позволяет обойтись без headless-браузера.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from selectolax.parser import HTMLParser

from . import fetch
from .config import City, Config
from .models import Car

log = logging.getLogger(__name__)


class ParseError(RuntimeError):
    """Страница получена, но карточек в ней нет — верстка изменилась."""


def _to_int(value) -> Optional[int]:
    """Числа из JSON приходят то строкой, то float — приводим аккуратно."""
    if value in (None, "", False):
        return None
    try:
        return int(round(float(str(value).replace(",", ".").replace(" ", ""))))
    except (TypeError, ValueError):
        return None


def _absolute(base_url: str, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def parse_cards(html: str, city: City, selectors: dict[str, str]) -> list[Car]:
    """Вытащить карточки машин из HTML страницы спеццен.

    Бросает ParseError, если карточек нет вовсе: пустой результат почти всегда
    означает сломанные селекторы, а не отсутствие скидок, и молча принимать
    его нельзя — иначе бот тихо перестанет постить.
    """
    tree = HTMLParser(html)
    nodes = tree.css(selectors["card"])
    if not nodes:
        raise ParseError(
            f"{city.name}: не найдено ни одной карточки по селектору "
            f"{selectors['card']!r} — вероятно, изменилась вёрстка сайта"
        )

    cars: list[Car] = []
    for node in nodes:
        id_node = node.css_first(selectors["car_id"])
        link_node = node.css_first(selectors["link"])
        image_node = node.css_first(selectors["image"])
        item_node = node.css_first(selectors["item"])

        car_id = id_node.attributes.get("data-car") if id_node else None
        href = link_node.attributes.get("href") if link_node else None
        if not car_id or not href:
            log.warning("%s: карточка без id или ссылки — пропускаю", city.name)
            continue

        name = None
        if image_node:
            name = image_node.attributes.get("alt") or image_node.attributes.get("title")

        discount_node = node.css_first("[data-discount-from]")
        cars.append(
            Car(
                city=city.name,
                car_id=car_id,
                name=(name or "").strip() or f"Автомобиль {car_id}",
                url=_absolute(city.base_url, href),
                image_url=_absolute(
                    city.base_url,
                    image_node.attributes.get("data-src") if image_node else None,
                ),
                base_price=_to_int(
                    item_node.attributes.get("static-price") if item_node else None
                ),
                discount_from=(
                    discount_node.attributes.get("data-discount-from") or None
                    if discount_node
                    else None
                ),
                discount_to=(
                    discount_node.attributes.get("data-discount-to") or None
                    if discount_node
                    else None
                ),
            )
        )
    return cars


def discount_from_payload(payload: dict) -> tuple[Optional[int], Optional[int], int]:
    """Посчитать (цена со скидкой, цена до скидки, процент) из ответа AJAX.

    Формула повторяет ту, что сайт применяет в браузере: доверяем полю
    discountTag, а если его нет — считаем процент из пары цен.
    """
    day_price = _to_int(payload.get("dayPrice"))
    old_price = _to_int(payload.get("oldDayPrice")) or _to_int(payload.get("originalPrice"))

    tag = _to_int(payload.get("discountTag"))
    if tag and tag > 0:
        percent = tag
    elif day_price and old_price and old_price > day_price:
        percent = round((1 - day_price / old_price) * 100)
    else:
        percent = 0

    if percent <= 0:
        old_price = None
    return day_price, old_price, percent


def enrich_prices(
    client: httpx.Client, config: Config, city: City, cars: list[Car]
) -> list[Car]:
    """Дозапросить цены по каждой машине и оставить только те, где есть скидка."""
    url = config.price_url(city)
    enriched: list[Car] = []
    for car in cars:
        payload = fetch.get_price(client, url, car.car_id)
        if not payload:
            continue
        day_price, old_price, percent = discount_from_payload(payload)
        if percent <= 0:
            log.info("%s / %s: скидки нет — в дайджест не берём", city.name, car.name)
            continue
        enriched.append(car.with_prices(day_price, old_price, percent))
    return enriched


def collect(client: httpx.Client, config: Config) -> list[Car]:
    """Собрать машины со скидкой по всем городам."""
    result: list[Car] = []
    for city in config.cities:
        html = fetch.get_page(client, config.promos_url(city))
        cards = parse_cards(html, city, config.selectors)
        log.info("%s: карточек на странице — %d", city.name, len(cards))
        result.extend(enrich_prices(client, config, city, cards))
    return result
