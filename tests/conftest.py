from __future__ import annotations

from pathlib import Path

import pytest

from avenue_bot.config import load_config
from avenue_bot.models import Promo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def cities(config):
    return {city.key: city for city in config.cities}


def load_fixture(city_key: str) -> str:
    return (FIXTURES / f"{city_key}.html").read_text(encoding="utf-8")


@pytest.fixture
def promo() -> Promo:
    return Promo(
        city_key="nsk",
        city_name="Новосибирск",
        city_name_in="Новосибирске",
        car_id="4586",
        title="Toyota Camry 70",
        url="https://avenuerent.ru/autopark/cars/car162/",
        image_url="https://avenuerent.ru/upload/iblock/526/photo.webp",
        body_type="седан",
        drive="передний",
        transmission="автомат",
        power="181 л.с.",
        engine="2,5 л",
        year="2019",
        labels=[],
        price=7000,
        price_source="calc",
    )
