"""Сборка текста поста из акции.

Тон и структура — по скиллу avenue-content для Telegram: эмодзи-навигация по
абзацам, один-два УТП, обязательный CTA в конце.

Ссылка на автомобиль вшита в его название: голые адреса в ленте выглядят
мусорно и занимают место, которого в подписи к фото всего 1024 символа.
Телеграм получает разметку HTML, MAX — тот же текст без тегов, и там ссылку
вшить некуда, поэтому для него адрес печатается отдельной строкой.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date

from .models import Promo

# Лимиты API: подпись к фото короче обычного сообщения, поэтому пост
# собирается по приоритетам и лишние строки отбрасываются (см. _assemble).
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024

MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

# УТП чередуются, чтобы посты подряд не выглядели под копирку.
# Выбор детерминированный (по ссылке на авто) — повторный прогон даёт тот же текст.
USP_LINES = [
    "Без залога, КАСКО уже в цене",
    "Доставим по адресу — в офис заезжать не нужно",
    "Бронь без предоплаты, поддержка 24/7",
    "Встретим в аэропорту, детское кресло — по запросу",
]


def format_price(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value:,}".replace(",", " ")


def format_date(value: str | None) -> str | None:
    """2026-12-31 -> «31 декабря 2026». Неразобранное значение возвращаем как есть."""
    if not value:
        return None
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value.strip())
    if not match:
        return value.strip()
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        return value.strip()
    return f"{day} {MONTHS_GENITIVE[month - 1]} {year}"


def _link(text: str, url: str, esc, html_links: bool) -> str:
    """Текст со вшитой ссылкой. Без поддержки разметки — просто текст."""
    if not html_links:
        return esc(text)
    return f'<a href="{html.escape(url, quote=True)}">{esc(text)}</a>'


def _period_line(promo: Promo, today: date | None = None) -> str | None:
    """Строка со сроками действия скидки."""
    today = today or date.today()
    start_raw, end_raw = promo.date_from, promo.date_to
    start, end = format_date(start_raw), format_date(end_raw)

    # Если скидка уже началась, «с такого-то» читателю не нужно.
    if start and _parse_iso(start_raw) and _parse_iso(start_raw) <= today:
        start = None

    if start and end:
        return f"📅 С {start} по {end}"
    if end:
        return f"📅 Действует до {end}"
    if start:
        return f"📅 Стартует {start}"
    return None


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _specs_line(promo: Promo) -> str | None:
    parts = []
    if promo.drive:
        parts.append(f"{promo.drive} привод")
    if promo.transmission:
        parts.append(promo.transmission)
    if promo.engine:
        parts.append(promo.engine)
    if promo.power:
        parts.append(promo.power)
    return "⚙️ " + ", ".join(parts) if parts else None


def _car_tail(promo: Promo) -> str:
    """Хвост названия: кузов и год."""
    return ", ".join(part for part in (promo.body_type, promo.year) if part)


def _car_line(promo: Promo) -> str:
    """Название автомобиля без разметки — для логов и dry-run."""
    tail = _car_tail(promo)
    return f"{promo.title} — {tail}" if tail else promo.title


def _price_line(promo: Promo) -> str | None:
    if promo.price is None:
        return None
    price = f"💰 {format_price(promo.price)} ₽/сут"
    if promo.old_price and promo.old_price > promo.price:
        price += f" вместо {format_price(promo.old_price)} ₽"
    if promo.discount_percent:
        price += f" — скидка {promo.discount_percent}%"
    return price


def _usp_line(promo: Promo) -> str:
    index = int(hashlib.sha256(promo.url.encode("utf-8")).hexdigest(), 16) % len(USP_LINES)
    return f"✅ {USP_LINES[index]}"


def _headline(promo: Promo) -> str:
    if promo.discount_percent:
        return f"🔥 Скидка {promo.discount_percent}% в {promo.city_name_in}"
    return f"🔥 Авто со скидкой в {promo.city_name_in}"


def _assemble(promo: Promo, limit: int, escape: bool, html_links: bool) -> str:
    """Собрать пост, укладываясь в лимит.

    Если текст не влезает, сначала отбрасываются необязательные строки
    (сперва УТП, затем характеристики), и только потом режется название
    автомобиля — заголовок, цена и ссылка остаются всегда.

    Название режется до того, как вокруг него оборачивается тег ссылки:
    иначе обрезка разорвала бы <a href=...> и Telegram отказался бы принять
    сообщение целиком.
    """

    def esc(value: str) -> str:
        return html.escape(value, quote=False) if escape else value

    def compose(title: str, keep_specs: bool, keep_usp: bool) -> str:
        name = _link(title, promo.url, esc, html_links)
        tail = _car_tail(promo)
        car_block = f"{name} — {esc(tail)}" if tail else name
        if promo.labels:
            car_block += f"\n🏷 {esc(', '.join(promo.labels))}"

        blocks = [esc(_headline(promo)), car_block]

        specs = _specs_line(promo)
        if keep_specs and specs:
            blocks.append(esc(specs))

        facts = [line for line in (_price_line(promo), _period_line(promo)) if line]
        if facts:
            blocks.append("\n".join(esc(line) for line in facts))

        if keep_usp:
            blocks.append(esc(_usp_line(promo)))

        blocks.append(_cta(promo, esc, html_links))
        return "\n\n".join(blocks)

    for keep_specs, keep_usp in ((True, True), (True, False), (False, False)):
        text = compose(promo.title, keep_specs, keep_usp)
        if len(text) <= limit:
            return text

    # Остались одни обязательные блоки — укорачиваем название автомобиля.
    title = promo.title
    for _ in range(40):
        text = compose(title, keep_specs=False, keep_usp=False)
        if len(text) <= limit:
            return text
        shorter = _truncate(title, len(text) - limit)
        if shorter == title:
            break
        title = shorter
    return text[:limit]


def _cta(promo: Promo, esc, html_links: bool) -> str:
    """Кнопка действия в конце поста."""
    if html_links:
        return f"👉 {_link('Забронировать', promo.url, esc, True)}"
    return f"👉 {esc(promo.url)}"


def _truncate(text: str, overflow: int) -> str:
    """Укоротить строку на overflow символов, по возможности по границе слова."""
    keep = max(1, len(text) - overflow - 1)  # -1 под многоточие
    cut = text[:keep]
    space = cut.rfind(" ")
    if space > keep // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _digest_item(promo: Promo, esc, changed: bool, html_links: bool) -> str:
    """Одна позиция дайджеста: название со ссылкой и цена."""
    name = _link(promo.title, promo.url, esc, html_links)
    tail = _car_tail(promo)
    head = f"• {name} — {esc(tail)}" if tail else f"• {name}"

    price = ""
    if promo.price is not None:
        price = f"{format_price(promo.price)} ₽/сут"
        if changed and promo.previous_price and promo.previous_price > promo.price:
            # В блоке «обновились цены» процент считается от прошлой цены, а не
            # от базовой ставки сайта: иначе рядом с «было 4 050 ₽» стоял бы
            # процент от совсем другого числа и читался бы как ошибка.
            drop = round((1 - promo.price / promo.previous_price) * 100)
            price += f" — было {format_price(promo.previous_price)} ₽"
            if drop:
                price += f" (−{drop}%)"
        else:
            if promo.old_price and promo.old_price > promo.price:
                price += f" вместо {format_price(promo.old_price)} ₽"
            if promo.discount_percent:
                price += f" (−{promo.discount_percent}%)"

    lines = [head]
    if price:
        lines.append(f"  {esc(price)}")
    if not html_links:
        # Разметки нет — адрес приходится печатать отдельной строкой.
        lines.append(f"  {esc(promo.url)}")
    return "\n".join(lines)


def _group_by_city(promos: list[Promo]) -> dict[str, list[Promo]]:
    grouped: dict[str, list[Promo]] = {}
    for promo in promos:
        grouped.setdefault(promo.city_name, []).append(promo)
    return grouped


def _digest_headline(new: list[Promo], changed: list[Promo]) -> str:
    if new and changed:
        return "🔥 Что нового в акциях «Авеню»"
    if changed:
        return f"💰 Цены снизились — {len(changed)} {_cars_word(len(changed))}"
    return f"🔥 Новые авто со скидкой — {len(new)} {_cars_word(len(new))}"


def _digest_text(
    new: list[Promo], changed: list[Promo], escape: bool, html_links: bool
) -> str:
    """Собрать дайджест целиком, без учёта лимита длины."""

    def esc(value: str) -> str:
        return html.escape(value, quote=False) if escape else value

    sections: list[str] = [esc(_digest_headline(new, changed))]

    if new:
        for city_name, promos in _group_by_city(new).items():
            body = "\n".join(
                _digest_item(promo, esc, False, html_links) for promo in promos
            )
            sections.append(f"📍 {esc(city_name)}\n{body}")

    if changed:
        # Отдельный подзаголовок нужен, только когда выше уже есть новинки:
        # если дайджест целиком про снижение цен, об этом сказано в заголовке.
        if new:
            sections.append(esc("💰 Обновились цены"))
        for city_name, promos in _group_by_city(changed).items():
            body = "\n".join(
                _digest_item(promo, esc, True, html_links) for promo in promos
            )
            sections.append(f"📍 {esc(city_name)}\n{body}")

    sections.append(esc("✅ Без залога, КАСКО в цене, доставим по адресу"))

    # Ссылку «все акции» ставим, только если дайджест про один город —
    # иначе она увела бы читателя не на ту страницу.
    cities = {promo.city_promos_url for promo in new + changed if promo.city_promos_url}
    if len(cities) == 1:
        url = cities.pop()
        if html_links:
            sections.append(f"👉 {_link('Все акции на сайте', url, esc, True)}")
        else:
            sections.append(f"👉 Все акции: {esc(url)}")

    return "\n\n".join(sections)


def _render_digest(
    new: list[Promo],
    changed: list[Promo],
    limit: int,
    escape: bool,
    html_links: bool,
) -> str:
    """Один пост со всеми изменениями за прогон.

    Вызывается только когда есть что показать: пустой дайджест в канал
    не уходит, эту проверку делает main.py.
    """
    text = _digest_text(new, changed, escape, html_links)
    if len(text) <= limit:
        return text

    # Не влезло в лимит — выкидываем позиции с конца (сначала подешевевшие,
    # они менее интересны) и дописываем, сколько осталось за кадром.
    keep_new, keep_changed = list(new), list(changed)
    while keep_new or keep_changed:
        if keep_changed:
            keep_changed.pop()
        else:
            keep_new.pop()
        if not keep_new and not keep_changed:
            break
        hidden = (len(new) - len(keep_new)) + (len(changed) - len(keep_changed))
        tail = f"\n\nИ ещё {hidden} {_cars_word(hidden)} — на сайте."
        text = _digest_text(keep_new, keep_changed, escape, html_links) + tail
        if len(text) <= limit:
            return text

    # Даже одна позиция не влезла — отдаём хотя бы заголовок.
    return _digest_headline(new, changed)[:limit]


def _cars_word(count: int) -> str:
    if 11 <= count % 100 <= 14:
        return "автомобилей"
    return {1: "автомобиль", 2: "автомобиля", 3: "автомобиля", 4: "автомобиля"}.get(
        count % 10, "автомобилей"
    )


def render_digest_telegram(new: list[Promo], changed: list[Promo]) -> str:
    return _render_digest(
        new, changed, TELEGRAM_MESSAGE_LIMIT, escape=True, html_links=True
    )


def render_digest_max(new: list[Promo], changed: list[Promo], limit: int = 4000) -> str:
    return _render_digest(new, changed, limit, escape=False, html_links=False)


def render_telegram(promo: Promo, with_photo: bool | None = None) -> str:
    """Текст поста для Telegram (parse_mode=HTML)."""
    if with_photo is None:
        with_photo = bool(promo.image_url)
    limit = TELEGRAM_CAPTION_LIMIT if with_photo else TELEGRAM_MESSAGE_LIMIT
    return _assemble(promo, limit, escape=True, html_links=True)


def render_max(promo: Promo, limit: int = 4000) -> str:
    """Текст поста для MAX — без HTML-разметки."""
    return _assemble(promo, limit, escape=False, html_links=False)
