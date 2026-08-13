"""Отправка в мессенджер MAX — заглушка.

Подключение отложено до регистрации бота от юрлица на business.max.ru
(с августа 2025 физлицам и ИП боты в MAX не выдают).

Чтобы включить, когда токен появится:
  1. В config.yml поставить max.enabled: true
  2. Добавить секреты MAX_BOT_TOKEN и MAX_CHAT_ID в GitHub
  3. Реализовать send() ниже: POST {api_base}/messages с заголовком
     Authorization: Bearer <token>. Актуальный домен и формат тела
     сверить по dev.max.ru — прежний botapi.max.ru объявлен устаревшим.
     Если альбомы не поддерживаются, слать текст + первое фото.

Остальной код к этому готов: main вызывает отправителей независимо,
и падение MAX не мешает публикации в Telegram.
"""
from __future__ import annotations

import logging

import httpx

from ..config import Config
from ..render import Post

log = logging.getLogger(__name__)


def send(client: httpx.Client, config: Config, posts: list[Post]) -> None:
    log.info("MAX отключён в конфиге — пропускаю %d сообщение(й)", len(posts))
