"""Отправка дайджеста в Telegram.

Фото на сайте лежат в формате .webp, а Telegram принимает его по ссылке не
всегда. Поэтому сначала пробуем отдать ссылки (быстро и без трафика), а если
API ругается — скачиваем картинки, конвертируем в JPEG и грузим файлами.
"""
from __future__ import annotations

import io
import json
import logging
import time
from typing import Optional

import httpx

from ..config import Config
from ..render import Post

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
PAUSE_BETWEEN_POSTS = 1.0


class TelegramError(RuntimeError):
    pass


def _call(
    client: httpx.Client,
    token: str,
    method: str,
    data: dict,
    files: Optional[dict] = None,
) -> tuple[bool, str]:
    """Вызвать метод Bot API. Возвращает (успех, описание ошибки)."""
    try:
        response = client.post(f"{API}/bot{token}/{method}", data=data, files=files)
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — сеть или не-JSON в ответе
        return False, str(exc)
    if payload.get("ok"):
        return True, ""
    return False, str(payload.get("description", "неизвестная ошибка"))


def _as_jpeg(client: httpx.Client, url: str) -> Optional[bytes]:
    """Скачать картинку и перекодировать в JPEG."""
    try:
        from PIL import Image

        response = client.get(url)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — битая картинка не должна ронять пост
        log.warning("не удалось перекодировать %s: %s", url, exc)
        return None


def _send_by_url(client: httpx.Client, config: Config, post: Post) -> tuple[bool, str]:
    chat_id = config.telegram_chat_id
    if not post.images:
        return _call(client, config.telegram_token, "sendMessage", {
            "chat_id": chat_id,
            "text": post.caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
    if len(post.images) == 1:
        return _call(client, config.telegram_token, "sendPhoto", {
            "chat_id": chat_id,
            "photo": post.images[0],
            "caption": post.caption,
            "parse_mode": "HTML",
        })
    media = [{"type": "photo", "media": url} for url in post.images]
    media[0]["caption"] = post.caption
    media[0]["parse_mode"] = "HTML"
    return _call(client, config.telegram_token, "sendMediaGroup", {
        "chat_id": chat_id,
        "media": json.dumps(media, ensure_ascii=False),
    })


def _send_by_upload(client: httpx.Client, config: Config, post: Post) -> tuple[bool, str]:
    """Запасной путь: перекодировать картинки в JPEG и отправить файлами."""
    chat_id = config.telegram_chat_id
    blobs = [(url, _as_jpeg(client, url)) for url in post.images]
    blobs = [(url, data) for url, data in blobs if data]
    if not blobs:
        log.warning("ни одну картинку перекодировать не удалось — шлём текстом")
        return _call(client, config.telegram_token, "sendMessage", {
            "chat_id": chat_id,
            "text": post.caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })

    if len(blobs) == 1:
        return _call(
            client, config.telegram_token, "sendPhoto",
            {"chat_id": chat_id, "caption": post.caption, "parse_mode": "HTML"},
            files={"photo": ("car.jpg", blobs[0][1], "image/jpeg")},
        )

    files, media = {}, []
    for index, (_, data) in enumerate(blobs):
        field = f"photo{index}"
        files[field] = (f"{field}.jpg", data, "image/jpeg")
        media.append({"type": "photo", "media": f"attach://{field}"})
    media[0]["caption"] = post.caption
    media[0]["parse_mode"] = "HTML"
    return _call(
        client, config.telegram_token, "sendMediaGroup",
        {"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
        files=files,
    )


def send(client: httpx.Client, config: Config, posts: list[Post]) -> None:
    """Отправить дайджест. Бросает TelegramError, если сообщение не ушло."""
    for index, post in enumerate(posts):
        ok, error = _send_by_url(client, config, post)
        if not ok and post.images:
            log.warning("отправка по ссылке не удалась (%s) — пробую загрузкой файлов", error)
            ok, error = _send_by_upload(client, config, post)
        if not ok:
            raise TelegramError(f"Telegram отклонил сообщение {index + 1}: {error}")
        log.info("сообщение %d/%d отправлено (%d фото)",
                 index + 1, len(posts), len(post.images))
        if index + 1 < len(posts):
            time.sleep(PAUSE_BETWEEN_POSTS)
