"""Уведомления о сбоях в служебный чат.

Поломка парсера иначе остаётся незамеченной: бот просто перестаёт постить,
и об этом узнают через недели. Если TELEGRAM_ADMIN_CHAT_ID не задан,
сообщение уходит в лог — красный прогон в Actions остаётся единственным сигналом.
"""
from __future__ import annotations

import logging

import httpx

from .config import Config

log = logging.getLogger(__name__)


def notify(config: Config, text: str) -> None:
    log.error("АЛЕРТ: %s", text)
    if not (config.telegram_token and config.telegram_admin_chat_id):
        log.warning("служебный чат не настроен (TELEGRAM_ADMIN_CHAT_ID) — алерт только в лог")
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
            data={
                "chat_id": config.telegram_admin_chat_id,
                "text": f"⚠️ Бот акций «Авеню»\n\n{text}",
                "disable_web_page_preview": "true",
            },
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001 — алерт не должен ронять прогон
        log.error("не удалось отправить алерт: %s", exc)
