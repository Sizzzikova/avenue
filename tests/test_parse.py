"""Парсер на реальных страницах сайта, сохранённых 13.08.2026."""
from pathlib import Path

import pytest

from avenue_bot import config as config_module
from avenue_bot.config import City
from avenue_bot.parse import ParseError, discount_from_payload, parse_cards

FIXTURES = Path(__file__).parent / "fixtures"
CFG = config_module.load()

CASES = [
    ("aktcii-nsk.html", "Новосибирск", "https://avenuerent.ru", 3),
    ("aktcii-irkutsk.html", "Иркутск", "https://irkutsk.avenuerent.ru", 2),
    ("aktcii-gorno-altaysk.html", "Горно-Алтайск", "https://gorno-altaysk.avenuerent.ru", 2),
]


@pytest.mark.parametrize("filename,city_name,base_url,expected", CASES)
def test_card_count(filename, city_name, base_url, expected):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    cars = parse_cards(html, City(city_name, base_url), CFG.selectors)
    assert len(cars) == expected


@pytest.mark.parametrize("filename,city_name,base_url,expected", CASES)
def test_required_fields_present(filename, city_name, base_url, expected):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    cars = parse_cards(html, City(city_name, base_url), CFG.selectors)
    for car in cars:
        assert car.car_id.isdigit(), car
        assert car.name and not car.name.startswith("Автомобиль "), car
        assert car.url.startswith(f"{base_url}/autopark/cars/"), car
        assert car.image_url and car.image_url.startswith(f"{base_url}/upload/"), car
        assert car.base_price and car.base_price > 0, car


def test_known_car_parsed_exactly():
    html = (FIXTURES / "aktcii-nsk.html").read_text(encoding="utf-8")
    cars = parse_cards(html, City("Новосибирск", "https://avenuerent.ru"), CFG.selectors)
    exeed = next(c for c in cars if c.car_id == "6312")
    assert exeed.name == "Exeed TXL 4WD"
    assert exeed.base_price == 8400
    assert exeed.url == "https://avenuerent.ru/autopark/cars/Exeed-TXL-car713/"


def test_discount_period_is_read_when_present():
    html = (FIXTURES / "aktcii-gorno-altaysk.html").read_text(encoding="utf-8")
    cars = parse_cards(html, City("Горно-Алтайск", "https://gorno-altaysk.avenuerent.ru"), CFG.selectors)
    exeed = next(c for c in cars if c.car_id == "8264")
    assert exeed.discount_from == "2026-03-23"
    assert exeed.discount_to == "2026-12-31"


def test_empty_page_raises_instead_of_returning_nothing():
    """Ноль карточек — это сломанный парсер, а не «скидок нет»."""
    with pytest.raises(ParseError):
        parse_cards("<html><body><h1>Пусто</h1></body></html>",
                    City("Новосибирск", "https://avenuerent.ru"), CFG.selectors)


class TestDiscountCalculation:
    def test_uses_discount_tag_when_given(self):
        assert discount_from_payload(
            {"dayPrice": 6700, "oldDayPrice": 8400, "discountTag": 20}
        ) == (6700, 8400, 20)

    def test_computes_percent_when_tag_missing(self):
        day, old, percent = discount_from_payload({"dayPrice": 4000, "originalPrice": 5000})
        assert (day, old, percent) == (4000, 5000, 20)

    def test_no_discount_when_prices_equal(self):
        day, old, percent = discount_from_payload({"dayPrice": 5000, "oldDayPrice": 5000})
        assert percent == 0
        assert old is None

    def test_handles_string_and_float_values(self):
        assert discount_from_payload(
            {"dayPrice": "6700.0", "oldDayPrice": "8400", "discountTag": "20"}
        ) == (6700, 8400, 20)

    def test_survives_garbage_payload(self):
        assert discount_from_payload({}) == (None, None, 0)
