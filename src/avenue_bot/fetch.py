"""HTTP-слой: таймауты, ретраи с экспоненциальной паузой, браузерный User-Agent."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Страница не отдалась после всех попыток."""


class Fetcher:
    def __init__(self, http_config: dict[str, Any] | None = None) -> None:
        config = http_config or {}
        self.timeout = float(config.get("timeout_seconds", 30))
        self.retries = int(config.get("retries", 3))
        self.backoff = float(config.get("backoff_seconds", 2))
        self.user_agent = config.get("user_agent") or DEFAULT_USER_AGENT
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_text(self, url: str) -> str:
        """Скачать страницу. Бросает FetchError, если все попытки провалились."""
        response = self._request("GET", url)
        return response.text

    def post_json(self, url: str, data: dict[str, Any]) -> Any:
        """POST формы с разбором JSON-ответа."""
        response = self._request("POST", url, data=data)
        return response.json()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.StreamError) as error:
                last_error = error
                log.warning(
                    "%s %s — попытка %s/%s не удалась: %s",
                    method,
                    url,
                    attempt,
                    self.retries,
                    error,
                )
                if attempt < self.retries:
                    time.sleep(self.backoff * (2 ** (attempt - 1)))
        raise FetchError(f"{method} {url}: не удалось получить ответ ({last_error})")
