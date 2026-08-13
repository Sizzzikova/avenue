"""Сообщения в служебный чат: сбои и факт публикации.

Поломка парсера иначе остаётся незамеченной: бот просто перестаёт постить,
и об этом узнают через недели. Если TELEGRAM_ADMIN_CHAT_ID не задан,
сообщение уходит в лог — красный прогон в Actions остаётся единственным сигналом.

В дни без изменений сюда ничего не приходит: сбой даёт и сообщение, и красный
прогон, поэтому тишина однозначно читается как «проверено, изменений нет».
"""
from __future__ import annotations

import logging

import httpx

from .config import Config

log = logging.getLogger(__name__)

TITLE = "Бот акций «Авеню»"


def _send(config: Config, text: str) -> None:
    """Отправить сообщение в служебный чат, если он настроен.

    Собственные ошибки только логируются: уведомление второстепенно по отношению
    к публикации и не должно ронять прогон.
    """
    if not (config.telegram_token and config.telegram_admin_chat_id):
        log.warning("служебный чат не настроен (TELEGRAM_ADMIN_CHAT_ID) — только в лог")
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
            data={
                "chat_id": config.telegram_admin_chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            },
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001 — уведомление не должно ронять прогон
        log.error("не удалось отправить сообщение в служебный чат: %s", exc)


def notify(config: Config, text: str) -> None:
    """Сообщить о сбое."""
    log.error("АЛЕРТ: %s", text)
    _send(config, f"⚠️ {TITLE}\n\n{text}")


def info(config: Config, text: str) -> None:
    """Сообщить о штатном событии — например, что дайджест опубликован."""
    log.info("%s", text)
    _send(config, f"✅ {TITLE}\n\n{text}")
