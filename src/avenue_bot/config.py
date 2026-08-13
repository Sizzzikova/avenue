"""Чтение config.yml и секретов из переменных окружения.

Секреты НИКОГДА не хранятся в репозитории — только в GitHub Secrets,
откуда workflow пробрасывает их в окружение процесса.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.yml"
DEFAULT_STATE = PROJECT_ROOT / "state" / "snapshot.json"


@dataclass(frozen=True)
class City:
    name: str
    base_url: str


@dataclass(frozen=True)
class Config:
    cities: list[City]
    promos_path: str
    price_ajax_path: str
    selectors: dict[str, str]
    footer: str
    telegram_enabled: bool
    media_group_limit: int
    caption_limit: int
    max_enabled: bool
    max_api_base: str

    # Секреты (пустые строки, если не заданы).
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    telegram_admin_chat_id: str = ""
    max_token: str = field(default="", repr=False)
    max_chat_id: str = ""

    def promos_url(self, city: City) -> str:
        return f"{city.base_url.rstrip('/')}{self.promos_path}"

    def price_url(self, city: City) -> str:
        return f"{city.base_url.rstrip('/')}{self.price_ajax_path}"

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_enabled and self.telegram_token and self.telegram_chat_id)


def load(path: Optional[Path] = None) -> Config:
    raw = yaml.safe_load((path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    tg = raw.get("telegram", {})
    mx = raw.get("max", {})
    return Config(
        cities=[City(name=c["name"], base_url=c["base_url"]) for c in raw["cities"]],
        promos_path=raw["promos_path"],
        price_ajax_path=raw["price_ajax_path"],
        selectors=raw["selectors"],
        footer=raw.get("footer", ""),
        telegram_enabled=bool(tg.get("enabled", False)),
        media_group_limit=int(tg.get("media_group_limit", 10)),
        caption_limit=int(tg.get("caption_limit", 1024)),
        max_enabled=bool(mx.get("enabled", False)),
        max_api_base=mx.get("api_base", ""),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        telegram_admin_chat_id=os.environ.get("TELEGRAM_ADMIN_CHAT_ID", ""),
        max_token=os.environ.get("MAX_BOT_TOKEN", ""),
        max_chat_id=os.environ.get("MAX_CHAT_ID", ""),
    )
