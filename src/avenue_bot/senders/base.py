"""Общий интерфейс отправителей."""

from __future__ import annotations

from typing import Protocol


class SendError(RuntimeError):
    """Мессенджер не принял пост."""


class Sender(Protocol):
    name: str

    def send_prepared(self, text: str, photo_url: str | None) -> None:
        """Опубликовать готовый текст. Бросает SendError, если не получилось.

        Текст готовится заранее (render.py) и сюда приходит уже собранным:
        так в канал уходит ровно то, что видел согласующий.
        """

    def send_text(self, text: str) -> None:
        """Служебное сообщение — алерты и уведомления."""
