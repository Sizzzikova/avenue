"""Извлечение акций (автомобилей со скидкой) со страницы /aktcii/.

Что важно знать про эту страницу:

* «Акция» на сайте «Авеню» — это не текстовый анонс, а карточка автомобиля
  в блоке «Автомобили со скидкой». Отсюда и состав полей модели Promo.
* Цена в HTML не лежит: вместо неё стоит заглушка «#N/A», а настоящее
  значение подставляет джаваскрипт запросом к /ajax/calc_catalog.php.
  Поэтому цену добирает prices.py, а здесь заполняется только запасной
  вариант из атрибута static-price.
* Микроразметки JSON-LD с офферами на странице нет (проверено на всех трёх
  городах: там только Organization/WebSite/Store), поэтому единственная
  стратегия — CSS-селекторы. Все они вынесены в config.yml.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from .config import City
from .models import Promo

log = logging.getLogger(__name__)


class ParseError(RuntimeError):
    """Разметка не распознана — вероятно, сайт переверстали."""


DRIVE_WORDS = {"передний", "задний", "полный"}
TRANSMISSION_WORDS = {"автомат", "механика", "робот", "вариатор", "механическая"}
POWER_RE = re.compile(r"л\.?\s*с\.?", re.IGNORECASE)
ENGINE_RE = re.compile(r"^\d+[.,]\d+\s*л$", re.IGNORECASE)


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(deep=True)).strip()


def parse_promos(html: str, city: City, selectors: dict[str, str]) -> list[Promo]:
    """Разобрать HTML страницы акций одного города.

    Пустой список — валидный результат только если на странице действительно
    нет карточек. Отличить это от сломанного парсера здесь нельзя, проверка
    живёт в main.py (см. «защита от тихой поломки»).
    """
    tree = HTMLParser(html)
    cards = tree.css(selectors["card"])
    log.info("%s: найдено карточек — %s", city.key, len(cards))

    promos: list[Promo] = []
    for card in cards:
        promo = _parse_card(card, city, selectors)
        if promo is not None:
            promos.append(promo)
    return promos


def _parse_card(card: Node, city: City, selectors: dict[str, str]) -> Promo | None:
    data_anchor = card.css_first(selectors["data_anchor"])
    title_link = card.css_first(selectors["title_link"])

    # Ссылка на авто: сначала из data-атрибута, потом из заголовка.
    href = None
    if data_anchor is not None:
        href = data_anchor.attributes.get("data-link") or data_anchor.attributes.get("href")
    if not href and title_link is not None:
        href = title_link.attributes.get("href")
    if not href:
        log.warning("%s: карточка без ссылки на авто, пропускаем", city.key)
        return None

    title = _text(title_link)
    if not title and data_anchor is not None:
        title = (data_anchor.attributes.get("data-car-name") or "").strip()
    if not title:
        log.warning("%s: карточка без названия (%s), пропускаем", city.key, href)
        return None

    car_id = ""
    date_from = date_to = None
    if data_anchor is not None:
        car_id = (data_anchor.attributes.get("data-car") or "").strip()
        date_from = (data_anchor.attributes.get("data-discount-from") or "").strip() or None
        date_to = (data_anchor.attributes.get("data-discount-to") or "").strip() or None

    promo = Promo(
        city_key=city.key,
        city_name=city.name,
        city_name_in=city.name_in,
        car_id=car_id,
        title=title,
        url=urljoin(city.base_url, href),
        city_promos_url=city.promos_url,
        image_url=_first_image(card, city, selectors),
        body_type=_text(card.css_first(selectors["body_type"])) or None,
        year=_text(card.css_first(selectors["year"])) or None,
        labels=[t for t in (_text(node) for node in card.css(selectors["labels"])) if t],
        price=_static_price(card),
        date_from=date_from,
        date_to=date_to,
    )
    _fill_properties(promo, card, selectors)
    return promo


def _first_image(card: Node, city: City, selectors: dict[str, str]) -> str | None:
    for img in card.css(selectors["image"]):
        src = img.attributes.get("data-src") or img.attributes.get("src")
        if src:
            return urljoin(city.base_url, src)
    return None


def _static_price(card: Node) -> int | None:
    """Базовая цена из атрибута static-price — запасной вариант, если ajax недоступен."""
    raw = (card.attributes.get("static-price") or "").strip()
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _fill_properties(promo: Promo, card: Node, selectors: dict[str, str]) -> None:
    """Разложить привод/коробку/мощность/объём по полям.

    Порядок этих спанов в вёрстке не гарантирован (у части авто нет мощности
    или объёма), поэтому определяем по содержимому, а не по позиции.
    """
    for node in card.css(selectors["properties"]):
        value = _text(node)
        if not value:
            continue
        low = value.lower()
        if low in DRIVE_WORDS:
            promo.drive = value
        elif low in TRANSMISSION_WORDS:
            promo.transmission = value
        elif POWER_RE.search(value):
            promo.power = value
        elif ENGINE_RE.match(value):
            promo.engine = value
