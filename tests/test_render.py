"""Формат поста: лимиты длины, экранирование, состав строк."""

from __future__ import annotations

import dataclasses

from avenue_bot.render import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    format_date,
    format_price,
    render_digest_max,
    render_digest_telegram,
    render_max,
    render_telegram,
)


def test_post_contains_city_car_price_and_link(promo):
    text = render_telegram(promo)
    assert "Новосибирске" in text
    assert "Toyota Camry 70" in text
    assert "7 000 ₽/сут" in text
    assert text.rstrip().endswith(promo.url)


def test_discount_goes_into_headline_and_price(promo):
    promo = dataclasses.replace(promo, price=7000, old_price=9000, discount_percent=22)
    text = render_telegram(promo)
    assert "Скидка 22%" in text
    assert "вместо 9 000 ₽" in text


def test_price_line_omitted_without_price(promo):
    promo = dataclasses.replace(promo, price=None)
    text = render_telegram(promo)
    assert "₽" not in text
    assert promo.url in text


def test_html_special_chars_are_escaped(promo):
    promo = dataclasses.replace(promo, title="Kia K5 <Luxe> & Co", image_url=None)
    text = render_telegram(promo)
    assert "&lt;Luxe&gt;" in text
    assert "&amp;" in text
    assert "<Luxe>" not in text


def test_max_text_has_no_html_entities(promo):
    promo = dataclasses.replace(promo, title="Kia K5 <Luxe> & Co")
    text = render_max(promo)
    assert "<Luxe> & Co" in text
    assert "&amp;" not in text


def test_caption_limit_respected_with_photo(promo):
    promo = dataclasses.replace(promo, title="Автомобиль " * 200)
    text = render_telegram(promo, with_photo=True)
    assert len(text) <= TELEGRAM_CAPTION_LIMIT


def test_message_limit_respected_without_photo(promo):
    promo = dataclasses.replace(promo, title="Автомобиль " * 2000)
    text = render_telegram(promo, with_photo=False)
    assert len(text) <= TELEGRAM_MESSAGE_LIMIT


def test_long_post_keeps_link_and_price(promo):
    """При обрезке первыми выбрасываются необязательные строки, не ссылка."""
    promo = dataclasses.replace(promo, title="Очень длинное название " * 40)
    text = render_telegram(promo, with_photo=True)
    assert len(text) <= TELEGRAM_CAPTION_LIMIT
    assert promo.url in text
    assert "7 000 ₽/сут" in text


def test_labels_are_shown(promo):
    promo = dataclasses.replace(promo, labels=["Новинка", "Правый руль"])
    assert "Новинка, Правый руль" in render_telegram(promo)


def test_period_line_for_future_range(promo):
    promo = dataclasses.replace(promo, date_from="2099-03-23", date_to="2099-12-31")
    text = render_telegram(promo)
    assert "С 23 марта 2099 по 31 декабря 2099" in text


def test_period_line_hides_past_start(promo):
    """Если скидка уже идёт, «с такого-то числа» читателю не нужно."""
    promo = dataclasses.replace(promo, date_from="2020-01-01", date_to="2099-12-31")
    text = render_telegram(promo)
    assert "Действует до 31 декабря 2099" in text
    assert "2020" not in text


def test_no_period_line_without_dates(promo):
    assert "📅" not in render_telegram(promo)


def test_format_price_uses_thin_grouping():
    assert format_price(1234567) == "1 234 567"
    assert format_price(None) == ""


def test_format_date_variants():
    assert format_date("2026-12-31") == "31 декабря 2026"
    assert format_date(None) is None
    assert format_date("скоро") == "скоро"
    assert format_date("2026-13-01") == "2026-13-01"


def test_same_promo_renders_identically(promo):
    """Выбор УТП детерминированный — повторный прогон даёт тот же текст."""
    assert render_telegram(promo) == render_telegram(promo)


# --- дайджест: один пост со всеми изменениями за сутки ---


def _promos(promo, count, city="Новосибирск"):
    return [
        dataclasses.replace(
            promo,
            title=f"Авто {index}",
            url=f"https://avenuerent.ru/autopark/cars/car{index}/",
            city_name=city,
            city_promos_url="https://avenuerent.ru/aktcii/",
        )
        for index in range(count)
    ]


def test_digest_lists_every_car(promo):
    text = render_digest_telegram(_promos(promo, 3), [])
    for index in range(3):
        assert f"Авто {index}" in text
        assert f"car{index}/" in text


def test_digest_groups_cars_by_city(promo):
    new = _promos(promo, 2) + _promos(promo, 1, city="Иркутск")
    text = render_digest_telegram(new, [])
    assert "📍 Новосибирск" in text
    assert "📍 Иркутск" in text


def test_digest_shows_previous_price_for_cheaper_cars(promo):
    cheaper = dataclasses.replace(promo, price=5900, previous_price=7000)
    text = render_digest_telegram([], [cheaper])
    assert "Обновились цены" not in text  # заголовок сам про снижение
    assert "Цены снизились" in text
    assert "5 900 ₽/сут — было 7 000 ₽" in text


def test_digest_separates_new_and_cheaper(promo):
    cheaper = dataclasses.replace(promo, title="Старое авто", price=5900, previous_price=7000)
    text = render_digest_telegram(_promos(promo, 1), [cheaper])
    assert "Что нового" in text
    assert "💰 Обновились цены" in text
    assert text.index("Авто 0") < text.index("Обновились цены")


def test_digest_links_to_promos_page_for_one_city(promo):
    text = render_digest_telegram(_promos(promo, 2), [])
    assert "👉 Все акции: https://avenuerent.ru/aktcii/" in text


def test_digest_omits_promos_link_for_several_cities(promo):
    """Одна ссылка «все акции» на несколько городов увела бы не туда."""
    new = _promos(promo, 1) + [
        dataclasses.replace(
            promo,
            city_name="Иркутск",
            url="https://irkutsk.avenuerent.ru/autopark/cars/car1/",
            city_promos_url="https://irkutsk.avenuerent.ru/aktcii/",
        )
    ]
    assert "Все акции" not in render_digest_telegram(new, [])


def test_digest_respects_message_limit(promo):
    text = render_digest_telegram(_promos(promo, 300), [])
    assert len(text) <= TELEGRAM_MESSAGE_LIMIT
    assert "И ещё" in text


def test_digest_escapes_html(promo):
    hacky = dataclasses.replace(promo, title="Kia <b>K5</b> & Co")
    text = render_digest_telegram([hacky], [])
    assert "&lt;b&gt;" in text
    assert "<b>" not in text


def test_digest_for_max_has_no_entities(promo):
    hacky = dataclasses.replace(promo, title="Kia K5 & Co")
    assert "&amp;" not in render_digest_max([hacky], [])
