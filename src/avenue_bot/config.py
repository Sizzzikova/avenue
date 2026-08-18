"""Чтение config.yml и секретов из переменных окружения.

Токены никогда не читаются из файлов конфигурации: репозиторий публичный,
секреты приходят только через окружение (GitHub Secrets).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Корень проекта: .../avenue-promo-bot
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yml"


@dataclass(frozen=True)
class City:
    key: str
    name: str
    name_in: str
    base_url: str
    promos_path: str

    @property
    def promos_url(self) -> str:
        return self.base_url.rstrip("/") + self.promos_path


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    # Он же чат согласования: посты с кнопками и алерты о поломках идут
    # в одно место — это рабочий чат, разделять его незачем.
    telegram_admin_chat_id: str | None
    max_bot_token: str | None
    max_chat_id: str | None

    @classmethod
    def from_env(cls) -> "Secrets":
        def get(name: str) -> str | None:
            value = os.environ.get(name, "").strip()
            return value or None

        return cls(
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get("TELEGRAM_CHAT_ID"),
            telegram_admin_chat_id=get("TELEGRAM_ADMIN_CHAT_ID"),
            max_bot_token=get("MAX_BOT_TOKEN"),
            max_chat_id=get("MAX_CHAT_ID"),
        )


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    path: Path

    @property
    def cities(self) -> list[City]:
        return [
            City(
                key=item["key"],
                name=item["name"],
                name_in=item.get("name_in", item["name"]),
                base_url=item["base_url"],
                promos_path=item.get("promos_path", "/aktcii/"),
            )
            for item in self.raw["cities"]
        ]

    @property
    def selectors(self) -> dict[str, str]:
        return self.raw["selectors"]

    @property
    def http(self) -> dict[str, Any]:
        return self.raw.get("http", {})

    @property
    def prices(self) -> dict[str, Any]:
        return self.raw.get("prices", {})

    @property
    def messengers(self) -> dict[str, Any]:
        return self.raw.get("messengers", {})

    @property
    def state_path(self) -> Path:
        configured = Path(self.raw.get("state", {}).get("path", "state/seen.json"))
        if configured.is_absolute():
            return configured
        return PROJECT_ROOT / configured

    @property
    def pending_path(self) -> Path:
        configured = Path(
            self.raw.get("state", {}).get("pending_path", "state/pending.json")
        )
        if configured.is_absolute():
            return configured
        return PROJECT_ROOT / configured

    @property
    def moderation(self) -> dict[str, Any]:
        return self.raw.get("moderation", {})

    @property
    def moderation_enabled(self) -> bool:
        return bool(self.moderation.get("enabled", False))

    @property
    def moderation_expire_hours(self) -> int:
        return int(self.moderation.get("expire_hours", 48))

    @property
    def post(self) -> dict[str, Any]:
        return self.raw.get("post", {})

    @property
    def post_mode(self) -> str:
        """digest — один пост со всеми изменениями, separate — пост на каждое авто."""
        mode = str(self.post.get("mode", "digest")).strip().lower()
        if mode not in {"digest", "separate"}:
            raise ValueError(
                f"post.mode должен быть digest или separate, а не {mode!r}"
            )
        return mode

    @property
    def include_changes(self) -> bool:
        """Считать ли изменением подешевевшее авто, а не только новое."""
        return bool(self.post.get("include_changes", True))

    @property
    def price_drop_percent(self) -> float:
        """На сколько процентов должна упасть цена, чтобы это стало поводом для поста."""
        value = float(self.post.get("price_drop_percent", 5))
        if value <= 0:
            raise ValueError("post.price_drop_percent должен быть больше нуля")
        return value


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(config_path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not raw or "cities" not in raw:
        raise ValueError(f"Конфиг {config_path} пустой или без секции cities")
    return Config(raw=raw, path=config_path)
