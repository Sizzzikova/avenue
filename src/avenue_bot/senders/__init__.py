"""Отправители постов. Каждый независим: падение одного не блокирует другой."""

from .base import SendError, Sender

__all__ = ["Sender", "SendError"]
