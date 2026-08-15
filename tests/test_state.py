"""Дедупликация: главная проверка — повторный прогон ничего не публикует."""

from __future__ import annotations

import dataclasses

import pytest

from avenue_bot.main import split_promos
from avenue_bot.state import State


@pytest.fixture
def state(tmp_path):
    return State.load(tmp_path / "seen.json")


def test_first_run_treats_everything_as_new(state, promo):
    new, changed = split_promos([promo], state, include_changes=True)
    assert new == [promo]
    assert changed == []


def test_second_run_finds_nothing(state, promo):
    new, _ = split_promos([promo], state, include_changes=True)
    for candidate in new:
        state.mark_seen(candidate, posted=True)

    assert split_promos([promo], state, include_changes=True) == ([], [])


def test_price_drop_is_posted_after_confirmation(state, promo):
    state.mark_seen(promo, posted=True)
    cheaper = dataclasses.replace(promo, price=5900)  # −15.7%

    # Первый прогон только замечает снижение.
    assert split_promos([cheaper], state, include_changes=True) == ([], [])

    # На втором цена всё ещё низкая — значит это не разовый провал калькулятора.
    new, changed = split_promos([cheaper], state, include_changes=True)

    assert new == []
    assert changed == [cheaper]
    # Старая цена подставляется из состояния — для строки «было 7 000 ₽».
    assert cheaper.previous_price == 7000


def test_one_off_price_dip_never_gets_posted(state, promo):
    """Разовый провал калькулятора: назавтра цена вернулась — поста нет."""
    state.mark_seen(promo, posted=True)

    dip = dataclasses.replace(promo, price=5900)
    assert split_promos([dip], state, include_changes=True) == ([], [])

    # Цена вернулась на место.
    assert split_promos([promo], state, include_changes=True) == ([], [])

    # И повторный провал тоже сначала требует подтверждения.
    assert split_promos([dip], state, include_changes=True) == ([], [])


def test_small_price_wobble_is_silent(state, promo):
    """Колебание калькулятора на пару процентов — не новость."""
    state.mark_seen(promo, posted=True)
    wobble = dataclasses.replace(promo, price=6900)  # −1.4%

    assert split_promos([wobble], state, include_changes=True) == ([], [])
    # Точку отсчёта не двигаем, иначе накопившееся снижение осталось бы незамеченным.
    assert state.data[promo.key]["price"] == 7000


def test_small_drops_add_up_until_they_matter(state, promo):
    """Пять раз по проценту — это всё-таки снижение, и о нём надо сказать."""
    state.mark_seen(promo, posted=True)

    for price in (6930, 6860, 6790, 6720):  # каждый шаг меньше порога
        assert split_promos(
            [dataclasses.replace(promo, price=price)], state, include_changes=True
        ) == ([], [])

    crossed = dataclasses.replace(promo, price=6640)  # −5.1% от 7000
    assert split_promos([crossed], state, include_changes=True) == ([], [])

    _, changed = split_promos([crossed], state, include_changes=True)
    assert [item.previous_price for item in changed] == [7000]


def test_price_increase_is_silent_and_keeps_the_baseline(state, promo):
    """Подорожание не повод для поста и не двигает точку отсчёта.

    Иначе возврат к прежней цене бот объявил бы скидкой, хотя про подорожание
    подписчикам никто не говорил.
    """
    state.mark_seen(promo, posted=True)
    pricier = dataclasses.replace(promo, price=9900)

    assert split_promos([pricier], state, include_changes=True) == ([], [])
    assert state.data[pricier.key]["price"] == 7000

    # Вернулись к исходной цене — это не новая скидка.
    assert split_promos([promo], state, include_changes=True) == ([], [])


def test_prices_from_different_sources_are_never_compared(state, promo):
    """Калькулятор не ответил, подставилась цена из вёрстки — это не скидка."""
    state.mark_seen(promo, posted=True)
    fallback = dataclasses.replace(promo, price=4200, price_source="static")

    assert split_promos([fallback], state, include_changes=True) == ([], [])

    # Калькулятор снова заработал и вернул прежнюю цену — тоже тишина.
    assert split_promos([promo], state, include_changes=True) == ([], [])


def test_changes_ignored_when_disabled(state, promo):
    state.mark_seen(promo, posted=True)
    cheaper = dataclasses.replace(promo, price=5900)

    assert split_promos([cheaper], state, include_changes=False) == ([], [])


def test_cosmetic_edit_does_not_trigger_a_post(state, promo):
    """Правка плашки без снижения цены в канал не идёт."""
    state.mark_seen(promo, posted=True)
    edited = dataclasses.replace(promo, labels=["Новинка"])

    assert split_promos([edited], state, include_changes=True) == ([], [])
    assert state.data[edited.key]["fingerprint"] == edited.fingerprint


def test_same_car_in_two_cities_is_two_promos(state, promo):
    irkutsk = dataclasses.replace(
        promo,
        city_key="irkutsk",
        city_name="Иркутск",
        city_name_in="Иркутске",
        url="https://irkutsk.avenuerent.ru/autopark/cars/car162/",
    )
    assert promo.key != irkutsk.key

    new, _ = split_promos([promo, irkutsk], state, include_changes=True)
    assert len(new) == 2


def test_state_survives_save_and_load(tmp_path, promo):
    path = tmp_path / "seen.json"
    state = State.load(path)
    state.mark_seen(promo, posted=True)
    state.save()

    reloaded = State.load(path)
    assert not reloaded.is_new(promo)
    assert reloaded.data[promo.key]["posted_at"]
    assert reloaded.data[promo.key]["title"] == promo.title
    assert reloaded.baseline_price(promo) == 7000


def test_prune_removes_only_processed_cities(state, promo):
    irkutsk = dataclasses.replace(
        promo,
        city_key="irkutsk",
        city_name="Иркутск",
        url="https://irkutsk.avenuerent.ru/autopark/cars/car999/",
    )
    state.mark_seen(promo, posted=True)
    state.mark_seen(irkutsk, posted=True)

    # Новосибирск обработали, акция пропала; Иркутск не скачался — не трогаем.
    removed = state.prune(live_keys=set(), city_keys={"nsk"})

    assert removed == [promo.key]
    assert irkutsk.key in state.data


def test_legacy_url_keys_are_recognised_and_migrated(state, promo):
    """Состояние, записанное прошлой версией бота, не должно потеряться."""
    state.data[promo.legacy_key] = {
        "fingerprint": promo.fingerprint,
        "price": 7000,
        "price_source": "calc",
        "first_seen": "2026-08-01T00:00:00+00:00",
    }

    assert not state.is_new(promo)
    assert state.baseline_price(promo) == 7000

    state.migrate_keys([promo])

    assert promo.legacy_key not in state.data
    assert state.data[promo.key]["price"] == 7000
    assert not state.is_new(promo)


def test_migration_keeps_promos_out_of_prune(state, promo):
    """После переезда на новые ключи акция не должна выглядеть пропавшей."""
    state.data[promo.legacy_key] = {"fingerprint": promo.fingerprint, "price": 7000}
    state.migrate_keys([promo])

    removed = state.prune(live_keys={promo.key}, city_keys={promo.city_key})

    assert removed == []
    assert promo.key in state.data


def test_broken_state_file_raises(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{не json", encoding="utf-8")
    with pytest.raises(ValueError):
        State.load(path)
