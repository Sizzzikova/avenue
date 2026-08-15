"""Модель акции — автомобиля со скидкой на странице /aktcii/."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Promo:
    """Один автомобиль со скидкой на странице акций одного города."""

    city_key: str
    city_name: str
    city_name_in: str
    car_id: str
    title: str
    url: str
    # Страница акций города — ссылка «все акции» в дайджесте.
    city_promos_url: str = ""
    image_url: str | None = None
    body_type: str | None = None
    drive: str | None = None
    transmission: str | None = None
    power: str | None = None
    engine: str | None = None
    year: str | None = None
    labels: list[str] = field(default_factory=list)
    price: int | None = None
    # Откуда взялась цена: "calc" — калькулятор сайта, "static" — атрибут
    # static-price из вёрстки (запасной вариант, когда калькулятор не ответил).
    # Числа из разных источников сравнивать нельзя: это разные методики, а не
    # изменение предложения. См. main.split_promos.
    price_source: str = "static"
    old_price: int | None = None
    discount_percent: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    # Цена на прошлом прогоне: подставляется из состояния, когда акция
    # попала в дайджест как подешевевшая. В отпечаток не входит.
    previous_price: int | None = None

    @property
    def key(self) -> str:
        """Ключ в состоянии: город + внутренний id автомобиля.

        Id (data-car в вёрстке) переживает смену адреса страницы, а ссылка —
        нет: поправят слаг ради SEO, и бот сочтёт все машины новыми. Если id
        не нашёлся, откатываемся на ссылку.
        """
        if self.car_id:
            return f"{self.city_key}:car{self.car_id}"
        return f"{self.city_key}:{self.url}"

    @property
    def legacy_key(self) -> str:
        """Ключ старого формата — чтобы не потерять уже накопленное состояние."""
        return f"{self.city_key}:{self.url}"

    @property
    def fingerprint(self) -> str:
        """Отпечаток предложения: название, плашки, сроки скидки.

        Цены здесь СПЕЦИАЛЬНО нет. Она приходит из живого калькулятора сайта и
        гуляет сама по себе — от дат расчёта, от сезона, а при недоступности
        эндпоинта подменяется базовой ценой из вёрстки, то есть просто другим
        числом. Пока цена входила в отпечаток, любое такое колебание выглядело
        как «акцию поменяли». За движением цены следит отдельная логика в
        main.split_promos, с порогом и проверкой достоверности.
        """
        parts = [
            self.title,
            str(self.date_from),
            str(self.date_to),
            ",".join(self.labels),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
