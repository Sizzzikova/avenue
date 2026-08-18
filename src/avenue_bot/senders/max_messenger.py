"""Отправка постов в канал MAX.

ВНИМАНИЕ. Bot API мессенджера MAX ещё меняется: домен botapi.max.ru объявлен
устаревшим в пользу platform-api.max.ru, а способ передачи токена в разных
версиях документации отличается. Поэтому здесь:

* адрес API берётся из config.yml (messengers.max.api_url);
* токен уходит одновременно и заголовком Authorization: Bearer, и параметром
  access_token — какой из них сервер проигнорирует, не важно;
* картинка передаётся ссылкой в attachments и, если сервер её не принял,
  пост уходит обычным текстом (ссылка на авто в тексте остаётся).

Перед боевым запуском сверьтесь с dev.max.ru и при расхождении поправьте
_build_payload — остальной код от формата запроса не зависит.
"""

from __future__ import annotations

import logging

import httpx

from .base import SendError

log = logging.getLogger(__name__)


class MaxSender:
    name = "max"

    def __init__(
        self,
        token: str,
        chat_id: str,
        api_url: str = "https://platform-api.max.ru",
        timeout: float = 30.0,
    ) -> None:
        if not token or not chat_id:
            raise ValueError("Для MAX нужны MAX_BOT_TOKEN и MAX_CHAT_ID")
        self.token = token
        self.chat_id = chat_id
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def send_prepared(self, text: str, photo_url: str | None) -> None:
        """Опубликовать заранее подготовленный текст — тот самый, что согласовали."""
        if photo_url:
            try:
                self._post(self._build_payload(text, photo_url))
                return
            except SendError as error:
                log.warning("MAX: вложение не принято, отправляю текстом: %s", error)
        self._post(self._build_payload(text, None))

    def send_text(self, text: str) -> None:
        self._post(self._build_payload(text, None))

    def _build_payload(self, text: str, image_url: str | None) -> dict[str, object]:
        payload: dict[str, object] = {"text": text}
        if image_url:
            payload["attachments"] = [{"type": "image", "payload": {"url": image_url}}]
        return payload

    def _post(self, payload: dict[str, object]) -> dict:
        url = f"{self.api_url}/messages"
        try:
            response = self._client.post(
                url,
                json=payload,
                params={"chat_id": self.chat_id, "access_token": self.token},
                headers={"Authorization": f"Bearer {self.token}"},
            )
        except httpx.HTTPError as error:
            raise SendError(f"MAX: сеть недоступна ({error})") from error

        if response.status_code >= 400:
            raise SendError(f"MAX: HTTP {response.status_code} {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return {}
