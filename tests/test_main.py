"""Поведение при сбоях: сломанный парсер, недоступный сайт, пустые секреты."""

from __future__ import annotations

import pytest

from avenue_bot import main as main_module
from avenue_bot.config import Secrets
from avenue_bot.fetch import FetchError
from avenue_bot.main import build_senders, collect
from tests.conftest import load_fixture


class RecordingAlerter:
    def __init__(self):
        self.messages = []

    def notify(self, message):
        self.messages.append(message)

    def close(self):
        pass


class FakeFetcher:
    """Подменяет сеть: отдаёт заранее заданный HTML или бросает ошибку."""

    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_text(self, url):
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None:
            raise FetchError(f"нет заглушки для {url}")
        return page

    def post_json(self, url, data):
        raise FetchError("цены в тестах не запрашиваем")


@pytest.fixture
def patched_fetcher(monkeypatch):
    def install(pages):
        monkeypatch.setattr(main_module, "Fetcher", lambda http_config: FakeFetcher(pages))

    return install


def test_pages_are_parsed(config, patched_fetcher):
    city = config.cities[0]
    patched_fetcher({city.promos_url: load_fixture(city.key)})
    alerter = RecordingAlerter()

    results = collect(config, [city], alerter)

    assert results[0].ok
    assert len(results[0].promos) == 3
    assert alerter.messages == []


def test_zero_cards_on_a_live_page_is_treated_as_breakage(config, patched_fetcher):
    """Страница загрузилась, карточек ноль — это сломанный парсер, а не «акций нет»."""
    city = config.cities[0]
    patched_fetcher({city.promos_url: "<html><body><h1>Акции</h1></body></html>"})
    alerter = RecordingAlerter()

    results = collect(config, [city], alerter)

    assert not results[0].ok
    assert results[0].promos == []
    assert len(alerter.messages) == 1
    assert "вёрстка" in alerter.messages[0] or "селекторы" in alerter.messages[0]


def test_unreachable_site_alerts_and_marks_city_failed(config, patched_fetcher):
    city = config.cities[0]
    patched_fetcher({city.promos_url: FetchError("HTTP 403")})
    alerter = RecordingAlerter()

    results = collect(config, [city], alerter)

    assert not results[0].ok
    assert "недоступна" in alerter.messages[0]


def test_empty_body_alerts(config, patched_fetcher):
    city = config.cities[0]
    patched_fetcher({city.promos_url: "   "})
    alerter = RecordingAlerter()

    results = collect(config, [city], alerter)

    assert not results[0].ok
    assert "пустой ответ" in alerter.messages[0]


def test_one_broken_city_does_not_block_the_others(config, patched_fetcher):
    nsk, irkutsk = config.cities[0], config.cities[1]
    patched_fetcher(
        {
            nsk.promos_url: FetchError("таймаут"),
            irkutsk.promos_url: load_fixture(irkutsk.key),
        }
    )
    alerter = RecordingAlerter()

    results = collect(config, [nsk, irkutsk], alerter)

    assert [result.ok for result in results] == [False, True]
    assert len(results[1].promos) == 2


class FakeSender:
    name = "telegram"

    def __init__(self):
        self.sent = []    # (текст, ссылка на фото)
        self.copied = []  # (чат-источник, id сообщения)

    def send_prepared(self, text, photo_url):
        self.sent.append((text, photo_url))

    def copy_message(self, from_chat_id, message_id):
        self.copied.append((from_chat_id, message_id))

    def send_text(self, text):
        pass

    def close(self):
        pass

    @property
    def posts(self):
        return len(self.sent) + len(self.copied)

    @property
    def texts(self):
        return [text for text, _ in self.sent]


def _run(config, monkeypatch, tmp_path, mode, sender, allow_empty_state=True):
    """Прогнать полный сценарий на фикстурах, с подменённой сетью и отправителем."""
    import argparse

    pages = {
        city.promos_url: load_fixture(city.key) for city in config.cities
    }
    monkeypatch.setattr(main_module, "Fetcher", lambda http_config: FakeFetcher(pages))
    monkeypatch.setattr(main_module, "build_senders", lambda *_: [sender])
    monkeypatch.setattr(main_module, "load_config", lambda path=None: config)
    monkeypatch.setitem(config.raw["state"], "path", str(tmp_path / "seen.json"))
    monkeypatch.setitem(config.raw["messengers"]["telegram"], "delay_seconds", 0)
    monkeypatch.setitem(config.raw["moderation"], "enabled", False)

    return main_module.run(
        argparse.Namespace(
            mode=mode,
            config=None,
            city=None,
            verbose=False,
            allow_empty_state=allow_empty_state,
        )
    )


def test_digest_run_sends_one_post_then_goes_quiet(config, monkeypatch, tmp_path):
    """Главная проверка: сутки без изменений — в канал не уходит ничего."""
    sender = FakeSender()

    assert _run(config, monkeypatch, tmp_path, "run", sender) == 0
    # Все семь акций — одним постом, а не семью.
    assert sender.posts == 1
    text = sender.texts[0]
    assert text.count("<a href=") >= 7  # семь машин плюс ссылка «все акции»
    for city in ("Новосибирск", "Иркутск", "Горно-Алтайск"):
        assert city in text

    assert _run(config, monkeypatch, tmp_path, "run", sender) == 0
    assert sender.posts == 1  # второй прогон молчит


def test_single_change_is_posted_as_a_normal_post_with_photo(
    config, monkeypatch, tmp_path
):
    """Одно изменение — обычный пост с фото, а не дайджест из одной строки."""
    sender = FakeSender()
    nsk = config.cities[0]
    _run(config, monkeypatch, tmp_path, "seed", sender)

    # На странице появилась ещё одна машина: подменяем id у одной карточки.
    page = load_fixture(nsk.key).replace('data-car="4586"', 'data-car="9999"')
    monkeypatch.setattr(
        main_module, "Fetcher", lambda http_config: FakeFetcher({nsk.promos_url: page})
    )
    monkeypatch.setattr(main_module, "build_senders", lambda *_: [sender])

    import argparse

    assert (
        main_module.run(
            argparse.Namespace(
                mode="run",
                config=None,
                city=["nsk"],
                verbose=False,
                allow_empty_state=False,
            )
        )
        == 0
    )
    assert sender.posts == 1
    assert "car162" in sender.texts[0]


def test_changed_url_alone_is_not_a_new_car(config, monkeypatch, tmp_path):
    """Поправили слаг ради SEO — это не новая машина, состояние узнаёт её по id."""
    sender = FakeSender()
    nsk = config.cities[0]
    _run(config, monkeypatch, tmp_path, "seed", sender)

    page = load_fixture(nsk.key).replace("car162", "camry-70-novosibirsk")
    monkeypatch.setattr(
        main_module, "Fetcher", lambda http_config: FakeFetcher({nsk.promos_url: page})
    )
    monkeypatch.setattr(main_module, "build_senders", lambda *_: [sender])

    import argparse

    code = main_module.run(
        argparse.Namespace(
            mode="run", config=None, city=["nsk"], verbose=False, allow_empty_state=False
        )
    )

    assert code == 0
    assert sender.posts == 0


def test_separate_mode_posts_each_car(config, monkeypatch, tmp_path):
    sender = FakeSender()
    monkeypatch.setitem(config.raw["post"], "mode", "separate")

    assert _run(config, monkeypatch, tmp_path, "run", sender) == 0

    assert sender.posts == 7


def test_empty_state_stops_publication(config, monkeypatch, tmp_path):
    """Потерянное состояние не должно превращаться в залп из семи акций.

    Пустой seen.json в боевом режиме значит одно из двух: не делали seed или
    прошлый прогон не сохранил состояние. И то и другое — повод остановиться.
    """
    sender = FakeSender()

    code = _run(config, monkeypatch, tmp_path, "run", sender, allow_empty_state=False)

    assert code == 1
    assert sender.posts == 0


def test_empty_state_can_be_overridden_explicitly(config, monkeypatch, tmp_path):
    sender = FakeSender()

    code = _run(config, monkeypatch, tmp_path, "run", sender, allow_empty_state=True)

    assert code == 0
    assert sender.posts == 1


def test_seed_records_everything_without_posting(config, monkeypatch, tmp_path):
    sender = FakeSender()

    assert _run(config, monkeypatch, tmp_path, "seed", sender) == 0
    assert sender.posts == 0

    # После сида боевой прогон тоже молчит — акции уже помечены виденными.
    assert _run(config, monkeypatch, tmp_path, "run", sender) == 0
    assert sender.posts == 0


def test_dry_run_changes_nothing(config, monkeypatch, tmp_path, capsys):
    sender = FakeSender()

    assert _run(config, monkeypatch, tmp_path, "dry-run", sender) == 0
    assert sender.posts == 0
    assert not (tmp_path / "seen.json").exists()
    assert "Toyota Camry 70" in capsys.readouterr().out


def test_dry_run_says_when_there_is_nothing_to_post(config, monkeypatch, tmp_path, capsys):
    sender = FakeSender()
    _run(config, monkeypatch, tmp_path, "seed", sender)
    capsys.readouterr()

    assert _run(config, monkeypatch, tmp_path, "dry-run", sender) == 0
    assert "Изменений нет" in capsys.readouterr().out


def test_senders_are_skipped_without_secrets(config):
    empty = Secrets(None, None, None, None, None)
    assert build_senders(config, empty) == []


def test_telegram_sender_is_built_with_secrets(config):
    secrets = Secrets("123:token", "@avenue_rent", None, None, None)
    senders = build_senders(config, secrets)
    assert [sender.name for sender in senders] == ["telegram"]
    for sender in senders:
        sender.close()
