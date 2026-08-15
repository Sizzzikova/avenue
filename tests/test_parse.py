"""Парсер на сохранённых страницах всех трёх городов."""

from __future__ import annotations

import pytest

from avenue_bot.parse import parse_promos
from tests.conftest import load_fixture

# Сколько акций сейчас на каждой странице. Если сайт поменяется и фикстуры
# обновят, эти числа надо пересчитать — тест на то и нужен.
EXPECTED_COUNTS = {"nsk": 3, "irkutsk": 2, "gorno-altaysk": 2}


@pytest.mark.parametrize("city_key,expected", EXPECTED_COUNTS.items())
def test_promo_count(config, cities, city_key, expected):
    promos = parse_promos(load_fixture(city_key), cities[city_key], config.selectors)
    assert len(promos) == expected


@pytest.mark.parametrize("city_key", EXPECTED_COUNTS)
def test_required_fields_filled(config, cities, city_key):
    promos = parse_promos(load_fixture(city_key), cities[city_key], config.selectors)
    for promo in promos:
        assert promo.title
        assert promo.car_id.isdigit()
        assert promo.url.startswith(cities[city_key].base_url)
        assert promo.image_url and promo.image_url.startswith("https://")
        assert promo.body_type
        assert promo.year
        assert promo.transmission
        assert promo.drive
        assert promo.price and promo.price > 0


@pytest.mark.parametrize("city_key", EXPECTED_COUNTS)
def test_urls_are_absolute_and_city_scoped(config, cities, city_key):
    """Ссылки в HTML относительные — в посте должен быть домен нужного города."""
    promos = parse_promos(load_fixture(city_key), cities[city_key], config.selectors)
    for promo in promos:
        assert "/autopark/cars/" in promo.url
        assert promo.city_key == city_key


@pytest.mark.parametrize("city_key", EXPECTED_COUNTS)
def test_keys_are_unique(config, cities, city_key):
    promos = parse_promos(load_fixture(city_key), cities[city_key], config.selectors)
    assert len({promo.key for promo in promos}) == len(promos)


def test_properties_are_classified_not_positional(config, cities):
    """Привод/коробка/мощность/объём разложены по смыслу, а не по порядку."""
    promos = parse_promos(load_fixture("nsk"), cities["nsk"], config.selectors)
    camry = next(p for p in promos if p.title == "Toyota Camry 70")
    assert camry.drive == "передний"
    assert camry.transmission == "автомат"
    assert camry.power == "181 л.с."
    assert camry.engine == "2,5 л"
    assert camry.body_type == "седан"
    assert camry.year == "2019"


def test_labels_are_extracted(config, cities):
    promos = parse_promos(load_fixture("irkutsk"), cities["irkutsk"], config.selectors)
    roox = next(p for p in promos if p.title == "Nissan Roox")
    assert roox.labels == ["Правый руль"]
    # Мощность без завершающей точки («52 л.с») тоже должна распознаваться.
    assert roox.power == "52 л.с"


def test_discount_dates_are_extracted(config, cities):
    promos = parse_promos(
        load_fixture("gorno-altaysk"), cities["gorno-altaysk"], config.selectors
    )
    exeed = next(p for p in promos if p.title == "Exeed TXL 4WD")
    assert exeed.date_from == "2026-03-23"
    assert exeed.date_to == "2026-12-31"


def test_empty_page_returns_nothing(config, cities):
    """Пустой ответ не должен ронять парсер — на это реагирует main.py."""
    assert parse_promos("<html><body></body></html>", cities["nsk"], config.selectors) == []


def test_fingerprint_ignores_price(config, cities):
    """Цена гуляет сама по себе и в отпечаток не входит — иначе каждый
    пересчёт на сайте выглядел бы как «акцию поменяли»."""
    promos = parse_promos(load_fixture("nsk"), cities["nsk"], config.selectors)
    promo = promos[0]
    before = promo.fingerprint

    promo.price = (promo.price or 0) + 100
    promo.old_price = 99999
    promo.discount_percent = 42
    promo.price_source = "static"

    assert promo.fingerprint == before


def test_fingerprint_reacts_to_content_change(config, cities):
    promos = parse_promos(load_fixture("nsk"), cities["nsk"], config.selectors)
    promo = promos[0]

    before = promo.fingerprint
    promo.labels = ["Новинка"]
    assert promo.fingerprint != before

    before = promo.fingerprint
    promo.date_to = "2027-01-01"
    assert promo.fingerprint != before


def test_price_from_markup_is_marked_as_static(config, cities):
    """Без запроса к калькулятору цена считается взятой из вёрстки."""
    promos = parse_promos(load_fixture("nsk"), cities["nsk"], config.selectors)
    assert {promo.price_source for promo in promos} == {"static"}
