"""Черновики постов, ждущие согласования.

Бот живёт по расписанию и между запусками ничего не помнит, поэтому пост,
отправленный на согласование, приходится складывать на диск: нажатие кнопки
разберёт уже следующий запуск.

Хранится готовый текст, а не набор акций. Так опубликовано будет ровно то,
что человек видел глазами, даже если к моменту нажатия кнопки цены на сайте
успели измениться.

Формат state/pending.json:

    {
      "update_offset": 912345678,
      "drafts": {
        "a1b2c3d4": {
          "created_at": "2026-08-15T07:00:00+00:00",
          "status": "pending",
          "kind": "photo",
          "telegram_text": "...",
          "max_text": "...",
          "photo_url": "https://...",
          "review_message_id": 42
        }
      }
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Drafts:
    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        self.data = data or {"update_offset": 0, "drafts": {}}
        self.data.setdefault("update_offset", 0)
        self.data.setdefault("drafts", {})
        self.dirty = False

    @classmethod
    def load(cls, path: str | Path) -> "Drafts":
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as error:
            raise ValueError(f"Файл черновиков {path} повреждён: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"Файл черновиков {path} должен быть объектом JSON")
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        self.dirty = False

    # --- смещение в ленте обновлений Telegram ---

    @property
    def update_offset(self) -> int:
        value = self.data.get("update_offset", 0)
        return value if isinstance(value, int) else 0

    @update_offset.setter
    def update_offset(self, value: int) -> None:
        if value != self.update_offset:
            self.data["update_offset"] = value
            self.dirty = True

    # --- сами черновики ---

    @property
    def drafts(self) -> dict[str, dict[str, Any]]:
        return self.data["drafts"]

    def add(
        self,
        telegram_text: str,
        max_text: str,
        photo_url: str | None,
    ) -> str:
        """Положить черновик и вернуть его короткий идентификатор.

        Идентификатор уезжает в callback_data кнопки, а там всего 64 байта,
        поэтому это восемь символов хеша, а не что-то читаемое.
        """
        draft_id = hashlib.sha256(
            f"{_now().isoformat()}|{telegram_text}".encode("utf-8")
        ).hexdigest()[:8]
        self.drafts[draft_id] = {
            "created_at": _now().isoformat(timespec="seconds"),
            "status": PENDING,
            "kind": "photo" if photo_url else "text",
            "telegram_text": telegram_text,
            "max_text": max_text,
            "photo_url": photo_url,
            "review_message_id": None,
        }
        self.dirty = True
        return draft_id

    def get(self, draft_id: str) -> dict[str, Any] | None:
        return self.drafts.get(draft_id)

    def set_review_message(self, draft_id: str, message_id: int) -> None:
        draft = self.drafts.get(draft_id)
        if draft is not None:
            draft["review_message_id"] = message_id
            self.dirty = True

    def set_status(self, draft_id: str, status: str) -> None:
        draft = self.drafts.get(draft_id)
        if draft is not None and draft.get("status") != status:
            draft["status"] = status
            draft["decided_at"] = _now().isoformat(timespec="seconds")
            self.dirty = True

    def pending_ids(self) -> list[str]:
        return [
            draft_id
            for draft_id, draft in self.drafts.items()
            if draft.get("status") == PENDING
        ]

    def expire_old(self, hours: int) -> list[str]:
        """Пометить протухшие черновики, чтобы кнопки под ними не стреляли.

        Нажать «публиковать» через неделю после того, как пост подготовлен, —
        почти наверняка ошибка: цены к этому моменту другие.
        """
        deadline = _now() - timedelta(hours=hours)
        expired = []
        for draft_id in self.pending_ids():
            created = self.drafts[draft_id].get("created_at", "")
            try:
                created_at = datetime.fromisoformat(created)
            except ValueError:
                continue
            if created_at < deadline:
                self.set_status(draft_id, EXPIRED)
                expired.append(draft_id)
        return expired

    def forget_decided(self, keep_last: int = 50) -> None:
        """Подчистить архив решённых черновиков, чтобы файл не рос вечно."""
        decided = sorted(
            (
                (draft.get("decided_at", ""), draft_id)
                for draft_id, draft in self.drafts.items()
                if draft.get("status") != PENDING
            ),
            reverse=True,
        )
        for _, draft_id in decided[keep_last:]:
            del self.drafts[draft_id]
            self.dirty = True
