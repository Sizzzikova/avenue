"""Подстановка реальных цен и процента скидки.

В HTML страницы акций цены нет — там заглушка «#N/A». Сайт подставляет её
джаваскриптом: POST /ajax/calc_catalog.php с полем id=<id авто>, в ответ JSON
с dayPrice (цена со скидкой) и originalPrice (цена без скидки).

Эндпоинт стабильнее вёрстки, поэтому цена берётся оттуда, а разбор HTML
оставляет только запасное значение из static-price. Если запрос не удался,
акция всё равно публикуется — просто без указания старой цены и процента.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from .config import City
from .fetch import Fetcher
from .models import Promo

log = logging.getLogger(__name__)


def enrich_prices(
    promos: list[Promo],
    city: City,
    fetcher: Fetcher,
    prices_config: dict[str, Any],
) -> None:
    """Дописать в акции актуальную цену, старую цену и процент скидки (на месте)."""
    if not prices_config.get("enabled", True):
        return

    endpoint = urljoin(city.base_url, prices_config.get("path", "/ajax/calc_catalog.php"))
    for promo in promos:
        if not promo.car_id:
            continue
        try:
            payload = fetcher.post_json(endpoint, {"id": promo.car_id})
        except Exception as error:  # noqa: BLE001 — цена не критична, пост уйдёт и без неё
            log.warning("%s: цена для «%s» не получена: %s", city.key, promo.title, error)
            continue
        _apply_payload(promo, payload)


def _apply_payload(promo: Promo, payload: Any) -> None:
    if not isinstance(payload, dict):
        log.warning("Неожиданный формат ответа калькулятора для «%s»", promo.title)
        return

    day_price = _to_int(payload.get("dayPrice"))
    if day_price is None:
        return
    promo.price = day_price
    promo.price_source = "calc"

    original = _to_int(payload.get("originalPrice")) or _to_int(payload.get("oldDayPrice"))
    tag = _to_int(payload.get("discountTag"))

    if tag and tag > 0:
        promo.discount_percent = tag
        if original and original > day_price:
            promo.old_price = original
    elif original and original > day_price:
        promo.old_price = original
        promo.discount_percent = round((1 - day_price / original) * 100)


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(str(value).replace(",", ".").replace(" ", "")))
    except (TypeError, ValueError):
        return None
