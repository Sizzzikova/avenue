"""Снимок состояния: что было на сайте в прошлый раз.

Дайджест публикуется только при изменениях, поэтому мы храним не «что уже
отправлено», а полный слепок текущих скидок и его отпечаток. Совпал отпечаток —
значит на сайте ничего не поменялось, и бот молчит.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Car


@dataclass(frozen=True)
class Snapshot:
    cars: dict[str, dict]
    fingerprint: str
    posted_at: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not self.cars


EMPTY = Snapshot(cars={}, fingerprint="", posted_at=None)


def build(cars: list[Car]) -> Snapshot:
    """Свести список машин к сравнимому слепку.

    В отпечаток входят только те поля, изменение которых должно приводить к
    новому посту: состав машин, итоговая цена и процент. Название и фото
    хранятся для меток «новинка»/«скидка выросла», но на отпечаток не влияют —
    иначе замена фотографии на сайте вызывала бы лишний пост.
    """
    payload = {
        car.key: {
            "name": car.name,
            "day_price": car.day_price,
            "old_price": car.old_price,
            "discount_pct": car.discount_pct,
        }
        for car in sorted(cars, key=lambda c: c.key)
    }
    material = json.dumps(
        {k: {"day_price": v["day_price"], "discount_pct": v["discount_pct"]}
         for k, v in payload.items()},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return Snapshot(cars=payload, fingerprint=digest)


def changed(previous: Snapshot, current: Snapshot) -> bool:
    return previous.fingerprint != current.fingerprint


def mark(previous: Snapshot, car: Car) -> str:
    """Метка для строки дайджеста: новинка, выросшая скидка или ничего."""
    before = previous.cars.get(car.key)
    if before is None:
        return "🆕"
    was = before.get("discount_pct") or 0
    if car.discount_pct > was:
        return "📉"
    return ""


def load(path: Path) -> Snapshot:
    if not path.exists():
        return EMPTY
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return EMPTY
    return Snapshot(
        cars=raw.get("cars", {}),
        fingerprint=raw.get("fingerprint", ""),
        posted_at=raw.get("posted_at"),
    )


def save(path: Path, snapshot: Snapshot, posted: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cars": snapshot.cars,
        "fingerprint": snapshot.fingerprint,
        "posted_at": (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if posted
            else snapshot.posted_at
        ),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
