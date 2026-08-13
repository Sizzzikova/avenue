"""HTTP-слой: браузерный User-Agent, таймауты, ретраи с нарастающей паузой."""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# Сайт на Bitrix и на «голый» питоновский User-Agent может ответить иначе,
# чем браузеру, поэтому представляемся обычным Chrome.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
RETRY_DELAYS = (2, 4, 8)


class FetchError(RuntimeError):
    """Страница или эндпоинт недоступны после всех попыток."""


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )


def _with_retries(what: str, call) -> httpx.Response:
    last: Optional[Exception] = None
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            response = call()
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 — ретраим любую сетевую/HTTP-ошибку
            last = exc
            if delay is None:
                break
            log.warning("%s: попытка %d не удалась (%s), повтор через %ds",
                        what, attempt + 1, exc, delay)
            time.sleep(delay)
    raise FetchError(f"{what}: не удалось получить ответ ({last})") from last


def get_page(client: httpx.Client, url: str) -> str:
    """Скачать HTML страницы со спецценами."""
    response = _with_retries(f"GET {url}", lambda: client.get(url))
    return response.text


def get_price(client: httpx.Client, url: str, car_id: str) -> Optional[dict]:
    """Спросить у сайта актуальную цену машины.

    Возвращает разобранный JSON или None, если эндпоинт ответил не-JSON
    (например, отдал HTML-заглушку). Отсутствие цены не должно ронять весь
    прогон — машина просто уйдёт в дайджест без скидки и будет отфильтрована.
    """
    try:
        response = _with_retries(
            f"POST {url} (id={car_id})",
            lambda: client.post(url, files={"id": (None, car_id)}),
        )
    except FetchError as exc:
        log.warning("цена для id=%s не получена: %s", car_id, exc)
        return None
    try:
        return response.json()
    except ValueError:
        log.warning("цена для id=%s: ответ не JSON (%r...)", car_id, response.text[:120])
        return None
