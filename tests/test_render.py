"""Сборка дайджеста: лимиты Telegram, экранирование, порядок городов."""
from dataclasses import replace
from datetime import date

import pytest

from avenue_bot import config as config_module, state
from avenue_bot.models import Car
from avenue_bot.render import (
    build_digest, caption_length, car_line, format_date, format_price,
)

CFG = config_module.load()
TODAY = date(2026, 8, 13)


def car(city="Новосибирск", car_id="6312", name="Exeed TXL 4WD", **kwargs):
    defaults = dict(
        url=f"https://avenuerent.ru/autopark/cars/{car_id}/",
        image_url=f"https://avenuerent.ru/upload/{car_id}.webp",
        base_price=8400,
        day_price=6700,
        old_price=8400,
        discount_pct=20,
    )
    return Car(city=city, car_id=car_id, name=name, **{**defaults, **kwargs})


class TestFormatting:
    def test_price_uses_non_breaking_space(self):
        assert format_price(8400) == "8 400 ₽"

    def test_price_of_none_is_empty(self):
        assert format_price(None) == ""

    @pytest.mark.parametrize("value,expected", [
        ("2026-12-31", "31.12.2026"),
        ("", ""),
        (None, ""),
        ("скоро", "скоро"),
    ])
    def test_date(self, value, expected):
        assert format_date(value) == expected


class TestCaptionLength:
    """Telegram считает лимит по видимому тексту в кодовых единицах UTF-16."""

    def test_tags_do_not_count(self):
        assert caption_length('<a href="https://example.com/очень/длинный">ok</a>') == 2

    def test_entities_count_as_one_character(self):
        assert caption_length("&amp;") == 1

    def test_emoji_counts_as_two(self):
        assert caption_length("🚗") == 2

    def test_cyrillic_counts_as_one(self):
        assert caption_length("Авеню") == 5


class TestCarLine:
    def test_contains_link_price_and_percent(self):
        line = car_line(car())
        assert 'href="https://avenuerent.ru/autopark/cars/6312/"' in line
        assert "Exeed TXL 4WD" in line
        assert "<b>6 700 ₽</b>/сут" in line
        assert "−20%" in line

    def test_line_starts_with_dash(self):
        assert car_line(car()).startswith("- ")

    def test_old_price_is_not_shown(self):
        """Зачёркнутую цену до скидки заказчик просил убрать."""
        line = car_line(car(old_price=8400))
        assert "<s>" not in line
        assert "8 400" not in line

    def test_deadline_is_not_shown(self):
        assert "31.12.2026" not in car_line(car(discount_to="2026-12-31"))

    def test_html_special_chars_are_escaped(self):
        """Иначе '&' в названии модели уронит отправку с parse_mode=HTML."""
        line = car_line(car(name="Haval H6 <Premium> & Co"))
        assert "&lt;Premium&gt;" in line and "&amp; Co" in line
        assert "<Premium>" not in line


class TestDigest:
    def test_empty_input_produces_nothing(self):
        assert build_digest([], CFG, TODAY) == []

    def test_single_post_for_seven_cars(self):
        cars = [car(car_id=str(i)) for i in range(7)]
        posts = build_digest(cars, CFG, TODAY)
        assert len(posts) == 1
        assert len(posts[0].images) == 7

    def test_header_has_russian_date(self):
        posts = build_digest([car()], CFG, TODAY)
        assert "13 августа" in posts[0].caption

    def test_cities_follow_config_order(self):
        cars = [
            car(city="Горно-Алтайск", car_id="1"),
            car(city="Новосибирск", car_id="2"),
            car(city="Иркутск", car_id="3"),
        ]
        caption = build_digest(cars, CFG, TODAY)[0].caption
        assert (caption.index("Новосибирск")
                < caption.index("Иркутск")
                < caption.index("Горно-Алтайск"))

    def test_caption_never_exceeds_telegram_limit(self):
        cars = [car(car_id=str(i), name=f"Очень Длинное Название Модели {i}")
                for i in range(40)]
        for post in build_digest(cars, CFG, TODAY):
            assert caption_length(post.caption) <= CFG.caption_limit

    def test_album_never_exceeds_photo_limit(self):
        cars = [car(car_id=str(i)) for i in range(40)]
        for post in build_digest(cars, CFG, TODAY):
            assert len(post.images) <= CFG.media_group_limit

    def test_all_cars_survive_the_split(self):
        cars = [car(car_id=str(i), name=f"Модель {i}") for i in range(40)]
        posts = build_digest(cars, CFG, TODAY)
        assert len(posts) > 1
        rendered = "\n".join(p.caption for p in posts)
        for c in cars:
            assert c.name in rendered

    def test_cars_without_photo_still_appear(self):
        posts = build_digest([car(image_url=None)], CFG, TODAY)
        assert posts[0].images == []
        assert "Exeed TXL 4WD" in posts[0].caption

    def test_footer_is_included(self):
        posts = build_digest([car()], CFG, TODAY)
        assert CFG.footer in posts[0].caption
