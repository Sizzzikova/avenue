"""Модель автомобиля со спецценой."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class Car:
    city: str
    car_id: str
    name: str
    url: str
    image_url: Optional[str] = None
    base_price: Optional[int] = None
    day_price: Optional[int] = None
    old_price: Optional[int] = None
    discount_pct: int = 0
    discount_from: Optional[str] = None
    discount_to: Optional[str] = None

    @property
    def key(self) -> str:
        """Устойчивый ключ для сравнения снимков между запусками."""
        return f"{self.city}:{self.car_id}"

    def with_prices(
        self,
        day_price: Optional[int],
        old_price: Optional[int],
        discount_pct: int,
    ) -> "Car":
        return replace(
            self, day_price=day_price, old_price=old_price, discount_pct=discount_pct
        )
