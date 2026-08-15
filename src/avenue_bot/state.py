"""Состояние: какие акции уже опубликованы.

Формат state/seen.json:

    {
      "nsk:https://avenuerent.ru/autopark/cars/car162/": {
        "fingerprint": "...",
        "title": "Toyota Camry 70",
        "first_seen": "2026-08-13T09:00:00+00:00",
        "posted_at": "2026-08-13T09:00:01+00:00"
      }
    }

Файл коммитится обратно в репозиторий: другого хранилища у GitHub Actions нет,
а побочный плюс — регулярные коммиты не дают Actions отключить расписание
после 60 дней тишины.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Promo

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path, data: dict[str, dict[str, Any]] | None = None) -> None:
        self.path = path
        self.data: dict[str, dict[str, Any]] = data or {}
        self.dirty = False

    @classmethod
    def load(cls, path: str | Path) -> "State":
        path = Path(path)
        if not path.exists():
            log.info("Состояние %s не найдено — считаем, что запусков ещё не было", path)
            return cls(path)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as error:
            raise ValueError(f"Состояние {path} повреждено: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"Состояние {path} должно быть объектом JSON")
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        self.dirty = False

    def _entry(self, promo: Promo) -> dict[str, Any] | None:
        """Запись об акции — по нынешнему ключу или по ключу старого формата."""
        entry = self.data.get(promo.key)
        if entry is None:
            entry = self.data.get(promo.legacy_key)
        return entry

    def is_new(self, promo: Promo) -> bool:
        return self._entry(promo) is None

    def is_changed(self, promo: Promo) -> bool:
        entry = self._entry(promo)
        return entry is not None and entry.get("fingerprint") != promo.fingerprint

    def baseline_price(self, promo: Promo) -> int | None:
        """Цена, о которой в канале говорили в последний раз.

        Это не «цена вчера», а точка отсчёта: она сдвигается, только когда мы
        публикуем снижение или когда цена выросла. Так мелкие ежедневные
        колебания не сбрасывают отсчёт, накопившееся снижение всё-таки
        замечается, а качели вокруг одного значения — нет.

        None, если сравнивать не с чем или если сохранённая цена получена из
        другого источника — калькулятор и вёрстка дают разные числа, и их
        разница означала бы «сайт полежал», а не «цена изменилась».
        """
        entry = self._entry(promo)
        if entry is None:
            return None
        price = entry.get("price")
        if not isinstance(price, int):
            return None
        if entry.get("price_source") != promo.price_source:
            return None
        return price

    def pending_drop(self, promo: Promo) -> int | None:
        """Снижение, замеченное на прошлом прогоне, но ещё не подтверждённое."""
        entry = self._entry(promo)
        if entry is None:
            return None
        price = entry.get("pending_drop")
        return price if isinstance(price, int) else None

    def set_pending_drop(self, promo: Promo, price: int) -> None:
        entry = self._entry(promo)
        if entry is not None and entry.get("pending_drop") != price:
            entry["pending_drop"] = price
            self.dirty = True

    def clear_pending_drop(self, promo: Promo) -> None:
        entry = self._entry(promo)
        if entry is not None and "pending_drop" in entry:
            del entry["pending_drop"]
            self.dirty = True

    def update_baseline_price(self, promo: Promo) -> None:
        """Сдвинуть точку отсчёта к текущей цене, ничего не публикуя."""
        entry = self._entry(promo)
        if entry is None:
            return
        if entry.get("price") != promo.price or entry.get("price_source") != promo.price_source:
            entry["price"] = promo.price
            entry["price_source"] = promo.price_source
            self.dirty = True

    def mark_seen(self, promo: Promo, posted: bool) -> None:
        """Записать акцию как известную. posted=False — режим seed или обновление."""
        entry = self._entry(promo) or {}
        # Запись могла лежать под ключом старого формата — переносим на новый.
        self.data.pop(promo.legacy_key, None)
        entry["fingerprint"] = promo.fingerprint
        entry["title"] = promo.title
        entry["city"] = promo.city_name
        entry["price"] = promo.price
        entry["price_source"] = promo.price_source
        entry.pop("pending_drop", None)
        entry.setdefault("first_seen", _now())
        if posted:
            entry["posted_at"] = _now()
        self.data[promo.key] = entry
        self.dirty = True

    def migrate_keys(self, promos: list[Promo]) -> int:
        """Перевести записи со старых ключей (по ссылке) на новые (по id авто).

        Делается один раз, на первом же прогоне после обновления бота: иначе
        записи под старыми ключами выглядели бы как пропавшие акции, а сами
        акции — как новые.
        """
        moved = 0
        for promo in promos:
            if promo.key == promo.legacy_key:
                continue
            entry = self.data.pop(promo.legacy_key, None)
            if entry is None:
                continue
            self.data.setdefault(promo.key, entry)
            self.dirty = True
            moved += 1
        if moved:
            log.info("Состояние переведено на ключи по id автомобиля: %s записей", moved)
        return moved

    def prune(self, live_keys: set[str], city_keys: set[str]) -> list[str]:
        """Убрать акции, пропавшие со страниц успешно обработанных городов.

        Города, которые в этом прогоне не удалось скачать, не трогаем — иначе
        одна сетевая ошибка стёрла бы их историю и на следующем прогоне бот
        опубликовал бы всё заново.
        """
        removed = [
            key
            for key in self.data
            if key.split(":", 1)[0] in city_keys and key not in live_keys
        ]
        for key in removed:
            del self.data[key]
        if removed:
            self.dirty = True
        return removed
