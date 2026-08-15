"""Общий интерфейс отправителей."""

from __future__ import annotations

from typing import Protocol

from ..models import Promo


class SendError(RuntimeError):
    """Мессенджер не принял пост."""


class Sender(Protocol):
    name: str

    def send(self, promo: Promo) -> None:
        """Опубликовать одну акцию. Бросает SendError, если не получилось."""

    def send_digest(self, new: list[Promo], changed: list[Promo]) -> None:
        """Опубликовать один пост со всеми изменениями за прогон."""
