"""Формат поста: состав строк, лимиты длины, экранирование.

Пост — витрина-тизер: название автомобиля со вшитой ссылкой и размер скидки.
Ни года выпуска, ни характеристик, ни сумм: подробности ждут по ссылке,
а цены меняются, и обещать их в канале не стоит.
"""

from __future__ import annotations

import dataclasses

from avenue_bot.render import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    render_digest_max,
    render_digest_telegram,
    render_max,
    render_telegram,
)


def discounted(promo, percent=20, **kwargs):
    return dataclasses.replace(promo, discount_percent=percent, **kwargs)


# --- одиночный пост: одно изменение, идёт с фотографией ---


def test_post_shows_city_car_and_discount(promo):
    text = render_telegram(discounted(promo))
    assert "Скидка 20% в Новосибирске" in text
    assert f'<a href="{promo.url}">Toyota Camry 70</a>' in text


def test_post_has_no_year_specs_or_sums(promo):
    """Год, характеристики и цены из шаблона убраны."""
    text = render_telegram(discounted(promo))
    assert "2019" not in text
    assert "седан" not in text
    assert "181 л.с." not in text
    assert "автомат" not in text
    assert "₽" not in text
    assert "7 000" not in text


def test_post_without_discount_still_names_the_car(promo):
    text = render_telegram(promo)
    assert "Авто со скидкой в Новосибирске" in text
    assert "Toyota Camry 70" in text


def test_discount_is_not_repeated_twice(promo):
    """В заголовке процент уже назван — в строке с авто он не нужен."""
    text = render_telegram(discounted(promo))
    assert text.count("20%") == 1


def test_post_ends_with_a_call_to_action(promo):
    text = render_telegram(promo)
    assert text.rstrip().endswith(f'<a href="{promo.url}">Забронировать</a>')


def test_max_post_keeps_a_plain_url(promo):
    """В MAX разметки нет, поэтому адрес печатается как есть."""
    text = render_max(promo)
    assert "<a href" not in text
    assert text.rstrip().endswith(promo.url)


def test_labels_are_shown(promo):
    promo = dataclasses.replace(promo, labels=["Новинка", "Правый руль"])
    assert "Новинка, Правый руль" in render_telegram(promo)


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
    assert len(render_telegram(promo, with_photo=True)) <= TELEGRAM_CAPTION_LIMIT


def test_message_limit_respected_without_photo(promo):
    promo = dataclasses.replace(promo, title="Автомобиль " * 2000)
    assert len(render_telegram(promo, with_photo=False)) <= TELEGRAM_MESSAGE_LIMIT


def test_long_title_never_breaks_the_link(promo):
    """Название режется до оборачивания в тег, иначе Telegram не примет пост."""
    promo = dataclasses.replace(promo, title="Очень длинное название " * 40)
    text = render_telegram(promo, with_photo=True)
    assert len(text) <= TELEGRAM_CAPTION_LIMIT
    assert text.count("<a href=") == text.count("</a>")
    assert promo.url in text


def test_dates_never_appear_in_a_post(promo):
    """Дат в постах нет вообще — ни сроков скидки, ни года выпуска."""
    promo = dataclasses.replace(promo, date_from="2026-03-23", date_to="2026-12-31")
    text = render_telegram(promo)
    assert "📅" not in text
    assert "2026" not in text
    assert "декабря" not in text


def test_dates_never_appear_in_a_digest(promo):
    dated = dataclasses.replace(promo, date_from="2026-03-23", date_to="2026-12-31")
    text = render_digest_telegram([dated], [])
    assert "📅" not in text
    assert "2026" not in text


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
            discount_percent=10 + index,
            city_name=city,
            city_promos_url="https://avenuerent.ru/aktcii/",
        )
        for index in range(count)
    ]


def test_digest_lists_car_and_discount(promo):
    text = render_digest_telegram(_promos(promo, 3), [])
    for index in range(3):
        url = f"https://avenuerent.ru/autopark/cars/car{index}/"
        assert f'• <a href="{url}">Авто {index}</a> — скидка {10 + index}%' in text


def test_digest_has_no_year_or_sums(promo):
    text = render_digest_telegram(_promos(promo, 3), [])
    assert "2019" not in text
    assert "₽" not in text
    assert "седан" not in text


def test_digest_names_a_car_without_discount(promo):
    plain = dataclasses.replace(promo, discount_percent=None)
    text = render_digest_telegram([plain, *_promos(promo, 1)], [])
    assert "• <a href=" in text
    assert "Toyota Camry 70</a>\n" in text or "Toyota Camry 70</a>" in text


def test_digest_groups_cars_by_city(promo):
    new = _promos(promo, 2) + _promos(promo, 1, city="Иркутск")
    text = render_digest_telegram(new, [])
    assert "📍 Новосибирск" in text
    assert "📍 Иркутск" in text


def test_cheaper_car_shows_percent_not_sums(promo):
    """В блоке снижения тоже без сумм — только насколько подешевело."""
    cheaper = dataclasses.replace(promo, price=3825, previous_price=4050)
    text = render_digest_telegram([], [cheaper])
    assert "подешевел на 6%" in text
    assert "₽" not in text
    assert "4 050" not in text


def test_digest_separates_new_and_cheaper(promo):
    cheaper = dataclasses.replace(
        promo, title="Старое авто", price=5900, previous_price=7000
    )
    text = render_digest_telegram(_promos(promo, 1), [cheaper])
    assert "Что нового" in text
    assert "💰 Обновились цены" in text
    assert text.index("Авто 0") < text.index("Обновились цены")


def test_digest_links_to_promos_page_for_one_city(promo):
    text = render_digest_telegram(_promos(promo, 2), [])
    assert '<a href="https://avenuerent.ru/aktcii/">Все акции на сайте</a>' in text


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


def test_digest_for_max_prints_plain_urls(promo):
    text = render_digest_max(_promos(promo, 2), [])
    assert "<a href" not in text
    assert "  https://avenuerent.ru/autopark/cars/car0/" in text


def test_digest_respects_message_limit(promo):
    text = render_digest_telegram(_promos(promo, 300), [])
    assert len(text) <= TELEGRAM_MESSAGE_LIMIT
    assert "И ещё" in text


def test_digest_escapes_html(promo):
    hacky = dataclasses.replace(promo, title="Kia <b>K5</b> & Co")
    text = render_digest_telegram([hacky], [])
    assert "&lt;b&gt;" in text
    assert "<b>" not in text
