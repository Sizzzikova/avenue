"""Отправка постов в Telegram-канал через Bot API."""

from __future__ import annotations

import logging

import httpx

from ..models import Promo
from ..render import render_digest_telegram, render_telegram
from .base import SendError

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"


class TelegramSender:
    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout: float = 30.0,
        api_root: str = API_ROOT,
    ) -> None:
        if not token or not chat_id:
            raise ValueError("Для Telegram нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.api_root = api_root.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def send(self, promo: Promo) -> None:
        if promo.image_url:
            try:
                self._send_photo(promo)
                return
            except SendError as error:
                # Битая или неподдерживаемая картинка не должна съедать пост:
                # текст важнее, поэтому падаем обратно на обычное сообщение.
                log.warning("Фото не ушло (%s), отправляю текстом: %s", promo.title, error)
        self._send_message(promo)

    def send_digest(self, new: list[Promo], changed: list[Promo]) -> None:
        """Один пост со всеми изменениями. Фото нет — в дайджесте несколько авто."""
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": render_digest_telegram(new, changed),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    def send_text(self, text: str) -> None:
        """Служебное сообщение (алерты) — без разметки и превью ссылок."""
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    def _send_photo(self, promo: Promo) -> None:
        self._call(
            "sendPhoto",
            {
                "chat_id": self.chat_id,
                "photo": promo.image_url,
                "caption": render_telegram(promo, with_photo=True),
                "parse_mode": "HTML",
            },
        )

    def _send_message(self, promo: Promo) -> None:
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": render_telegram(promo, with_photo=False),
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )

    def _call(self, method: str, payload: dict[str, object]) -> dict:
        url = f"{self.api_root}/bot{self.token}/{method}"
        try:
            response = self._client.post(url, json=payload)
        except httpx.HTTPError as error:
            raise SendError(f"Telegram {method}: сеть недоступна ({error})") from error

        try:
            body = response.json()
        except ValueError:
            raise SendError(
                f"Telegram {method}: ответ не JSON (HTTP {response.status_code})"
            ) from None

        if not body.get("ok"):
            raise SendError(
                f"Telegram {method}: {body.get('error_code')} {body.get('description')}"
            )
        return body
