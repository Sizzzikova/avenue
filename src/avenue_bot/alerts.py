"""Уведомления в служебный чат.

Без этого поломка была бы тихой: акции просто перестали бы приходить в канал,
и заметили бы это через недели. Алерты идут в отдельный чат (TELEGRAM_ADMIN_CHAT_ID),
чтобы не мешать подписчикам.
"""

from __future__ import annotations

import logging

from .senders.telegram import TelegramSender

log = logging.getLogger(__name__)


class Alerter:
    def __init__(self, token: str | None, admin_chat_id: str | None) -> None:
        self._sender: TelegramSender | None = None
        if token and admin_chat_id:
            self._sender = TelegramSender(token, admin_chat_id)
        else:
            log.info("Служебный чат не настроен — алерты только в логе")

    def notify(self, message: str) -> None:
        log.error("ALERT: %s", message)
        if self._sender is None:
            return
        try:
            self._sender.send_text(f"⚠️ Бот акций «Авеню»\n\n{message}")
        except Exception as error:  # noqa: BLE001 — алерт не должен ронять прогон
            log.error("Не удалось отправить алерт: %s", error)

    def close(self) -> None:
        if self._sender is not None:
            self._sender.close()
