"""Отправка постов в Telegram-канал через Bot API."""

from __future__ import annotations

import logging

import httpx

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

    # --- согласование ---

    def send_for_review(
        self,
        chat_id: str,
        text: str,
        photo_url: str | None,
        draft_id: str,
    ) -> int:
        """Отправить пост на согласование и вернуть id сообщения.

        Пост уходит ровно в том виде, в каком попадёт в канал, — кнопки просто
        подвешены под ним. Так согласующий видит настоящий пост, а не пересказ.
        """
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Публиковать", "callback_data": f"pub:{draft_id}"},
                    {"text": "🚫 Не публиковать", "callback_data": f"rej:{draft_id}"},
                ]
            ]
        }
        if photo_url:
            body = self._call(
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": text,
                    "parse_mode": "HTML",
                    "reply_markup": keyboard,
                },
            )
        else:
            body = self._call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": keyboard,
                },
            )
        return int(body["result"]["message_id"])

    def get_callbacks(self, offset: int) -> tuple[list[dict], int]:
        """Забрать нажатия кнопок. Возвращает список и новое смещение.

        Смещение обязательно сохранять: Telegram отдаёт одни и те же обновления,
        пока их не подтвердишь, и без этого одобренный пост уходил бы в канал
        на каждом прогоне заново.
        """
        body = self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 0,
                "allowed_updates": ["callback_query"],
            },
        )
        updates = body.get("result", [])
        callbacks = [item["callback_query"] for item in updates if "callback_query" in item]
        next_offset = max((item["update_id"] for item in updates), default=offset - 1) + 1
        return callbacks, next_offset

    def answer_callback(self, callback_id: str, text: str) -> None:
        """Погасить «часики» на кнопке и показать всплывающий ответ."""
        try:
            self._call(
                "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
            )
        except SendError as error:
            # Ответ живёт несколько секунд; опоздали — не повод падать.
            log.warning("Не удалось ответить на нажатие: %s", error)

    def finish_review(
        self, chat_id: str, message_id: int, kind: str, text: str, verdict: str
    ) -> None:
        """Убрать кнопки и подписать, чем всё кончилось."""
        marked = f"{verdict}\n\n{text}"
        method = "editMessageCaption" if kind == "photo" else "editMessageText"
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []},
        }
        payload["caption" if kind == "photo" else "text"] = marked
        try:
            self._call(method, payload)
        except SendError as error:
            log.warning("Не удалось обновить сообщение согласования: %s", error)

    def send_prepared(self, text: str, photo_url: str | None) -> None:
        """Опубликовать заранее подготовленный текст — тот самый, что согласовали."""
        if photo_url:
            try:
                self._call(
                    "sendPhoto",
                    {
                        "chat_id": self.chat_id,
                        "photo": photo_url,
                        "caption": text,
                        "parse_mode": "HTML",
                    },
                )
                return
            except SendError as error:
                log.warning("Фото не ушло, отправляю текстом: %s", error)
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
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
