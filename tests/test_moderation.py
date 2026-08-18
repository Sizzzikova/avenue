"""Согласование: пост уходит в рабочий чат и ждёт кнопки."""

from __future__ import annotations

import argparse

import pytest

from avenue_bot import main as main_module
from avenue_bot.drafts import APPROVED, PENDING, REJECTED, Drafts
from tests.conftest import load_fixture
from tests.test_main import FakeFetcher, FakeSender


class FakeReviewer:
    """Подменяет Telegram в чате согласования."""

    name = "telegram"
    instances: list["FakeReviewer"] = []

    def __init__(self, token, chat_id, *args, **kwargs):
        self.reviewed = []      # (текст, фото, id черновика)
        self.answers = []       # всплывающие ответы на нажатие
        self.finished = []      # (id сообщения, вердикт)
        self.callbacks: list[dict] = []
        self.next_offset = 1
        self.published = []
        self.copied = []        # (чат-источник, id сообщения)
        self.confirmed = None   # подтверждённое смещение
        FakeReviewer.instances.append(self)

    def send_for_review(self, chat_id, text, photo_url, draft_id):
        self.reviewed.append((text, photo_url, draft_id))
        return 100 + len(self.reviewed)

    def get_callbacks(self, offset):
        return self.callbacks, self.next_offset

    def confirm_updates(self, offset):
        self.confirmed = offset

    def copy_message(self, from_chat_id, message_id):
        self.copied.append((from_chat_id, message_id))

    def answer_callback(self, callback_id, text):
        self.answers.append(text)

    def finish_review(self, chat_id, message_id, kind, text, verdict):
        self.finished.append((message_id, verdict))

    def send_prepared(self, text, photo_url):
        self.published.append((text, photo_url))

    def send_text(self, text):
        pass

    def close(self):
        pass


@pytest.fixture
def moderated(config, monkeypatch, tmp_path):
    """Прогон с включённым согласованием и подменённым Telegram."""
    FakeReviewer.instances.clear()
    pages = {city.promos_url: load_fixture(city.key) for city in config.cities}
    monkeypatch.setattr(main_module, "Fetcher", lambda http_config: FakeFetcher(pages))
    monkeypatch.setattr(main_module, "load_config", lambda path=None: config)
    monkeypatch.setattr("avenue_bot.senders.telegram.TelegramSender", FakeReviewer)
    monkeypatch.setitem(config.raw["state"], "path", str(tmp_path / "seen.json"))
    monkeypatch.setitem(config.raw["state"], "pending_path", str(tmp_path / "pending.json"))
    monkeypatch.setitem(config.raw["messengers"]["telegram"], "delay_seconds", 0)
    monkeypatch.setitem(config.raw["moderation"], "enabled", True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@avenue_channel")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "-100500")
    return config, tmp_path


def press(draft_id, action="pub", message_id=101, chat="-100500", text="🔥 Пост"):
    """Нажатие кнопки в том виде, в каком его присылает Telegram."""
    return {
        "id": f"cb-{draft_id}-{action}",
        "data": f"{action}:{draft_id}",
        "message": {
            "message_id": message_id,
            "chat": {"id": chat},
            "text": text,
        },
    }


def _run(mode, **kwargs):
    return main_module.run(
        argparse.Namespace(
            mode=mode,
            config=None,
            city=kwargs.get("city"),
            verbose=False,
            allow_empty_state=kwargs.get("allow_empty_state", True),
        )
    )


def test_post_goes_to_review_not_to_the_channel(moderated):
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        assert _run("run") == 0

    reviewer = FakeReviewer.instances[-1]
    assert len(reviewer.reviewed) == 1      # ушло на согласование
    assert channel.posts == 0               # в канал — ничего

    drafts = Drafts.load(tmp_path / "pending.json")
    assert len(drafts.pending_ids()) == 1


def test_approval_copies_the_reviewed_message_into_the_channel(moderated):
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("run")

        _, _, draft_id = FakeReviewer.instances[-1].reviewed[0]

        approver = FakeReviewer("t", "c")
        approver.callbacks = [press(draft_id)]
        approver.next_offset = 42
        patch.setattr("avenue_bot.senders.telegram.TelegramSender",
                      lambda *a, **k: approver)
        assert _run("approvals") == 0

    # В канал ушла копия того самого сообщения, что видел согласующий.
    assert channel.copied == [("-100500", 101)]
    assert approver.confirmed == 42  # обновления подтверждены до публикации
    assert approver.answers == ["Опубликовано"]
    assert approver.finished[0][1] == "✅ Опубликовано"

    drafts = Drafts.load(tmp_path / "pending.json")
    assert drafts.get(draft_id)["status"] == APPROVED
    assert drafts.update_offset == 42


def test_rejection_keeps_the_post_out_of_the_channel(moderated):
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("run")
        draft_id = FakeReviewer.instances[-1].reviewed[0][2]

        approver = FakeReviewer("t", "c")
        approver.callbacks = [press(draft_id, "rej")]
        patch.setattr("avenue_bot.senders.telegram.TelegramSender",
                      lambda *a, **k: approver)
        assert _run("approvals") == 0

    assert channel.posts == 0
    assert approver.answers == ["Не публикуем"]
    assert approver.finished[0][1] == "🚫 Отклонено, в канал не ушло"
    assert Drafts.load(tmp_path / "pending.json").get(draft_id)["status"] == REJECTED


def test_second_press_does_not_publish_twice(moderated):
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("run")
        draft_id = FakeReviewer.instances[-1].reviewed[0][2]

        approver = FakeReviewer("t", "c")
        approver.callbacks = [press(draft_id), press(draft_id)]
        patch.setattr("avenue_bot.senders.telegram.TelegramSender",
                      lambda *a, **k: approver)
        _run("approvals")

    assert channel.posts == 1
    assert approver.answers == ["Опубликовано", "По этому посту решение уже принято"]


def test_lost_draft_still_publishes_by_copying_the_message(moderated):
    """Файл черновиков не доехал — кнопка всё равно должна работать.

    Раньше нажатие в этом случае просто ничего не делало: бот не находил
    черновик и молча разводил руками.
    """
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        approver = FakeReviewer("t", "c")
        approver.callbacks = [press("deadbeef")]
        patch.setattr("avenue_bot.senders.telegram.TelegramSender",
                      lambda *a, **k: approver)
        assert _run("approvals") == 0

    assert channel.copied == [("-100500", 101)]
    assert approver.answers == ["Опубликовано"]


def test_approved_draft_is_not_offered_again_tomorrow(moderated):
    """Отправленное на согласование помечается виденным — второго черновика нет."""
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("run")
        assert len(FakeReviewer.instances[-1].reviewed) == 1

        # На втором прогоне отправлять нечего, поэтому Telegram даже не создаётся.
        FakeReviewer.instances.clear()
        _run("run")

    assert FakeReviewer.instances == []
    assert channel.posts == 0
    assert len(Drafts.load(tmp_path / "pending.json").pending_ids()) == 1


def test_expired_draft_cannot_be_published(moderated):
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("run")
        draft_id = FakeReviewer.instances[-1].reviewed[0][2]

        # Черновик пролежал дольше отведённого срока.
        drafts = Drafts.load(tmp_path / "pending.json")
        drafts.get(draft_id)["created_at"] = "2020-01-01T00:00:00+00:00"
        drafts.save()

        approver = FakeReviewer("t", "c")
        approver.callbacks = [{"id": "cb1", "data": f"pub:{draft_id}"}]
        patch.setattr("avenue_bot.senders.telegram.TelegramSender",
                      lambda *a, **k: approver)
        _run("approvals")

    assert channel.posts == 0
    assert "уже принято" in approver.answers[0]
    assert any("Просрочено" in verdict for _, verdict in approver.finished)


def test_moderation_off_publishes_straight_to_the_channel(moderated):
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(config.raw["moderation"], "enabled", False)
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        assert _run("run") == 0

    assert channel.posts == 1


def test_announce_posts_everything_regardless_of_memory(moderated):
    """Режим announce собирает пост из всех сегодняшних акций.

    Нужен, когда канал запускают заново: память уже полна, обычный run
    промолчал бы, а показать текущие цены надо.
    """
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])

        # Память полна: обычный прогон молчит.
        _run("seed")
        FakeReviewer.instances.clear()
        _run("run")
        assert FakeReviewer.instances == []

        # А announce всё равно готовит пост со всеми семью акциями.
        _run("announce")
        text = FakeReviewer.instances[-1].reviewed[0][0]

    assert text.count("<a href=") >= 7
    assert channel.posts == 0  # и тоже через согласование


def test_announce_refreshes_remembered_prices(moderated):
    """После announce память считается актуальной — назавтра тишина."""
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("announce")
        FakeReviewer.instances.clear()
        _run("run")

    assert FakeReviewer.instances == []


def test_empty_state_still_prepares_a_draft_when_moderation_is_on(moderated):
    """Пустая память при согласовании — не повод молчать.

    Залпа подписчикам всё равно не будет: пост уйдёт в рабочий чат под кнопки,
    и человек решит сам. Поэтому вместо отказа бот готовит черновик.
    """
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        code = _run("run", allow_empty_state=False)

    assert code == 0
    reviewer = FakeReviewer.instances[-1]
    assert len(reviewer.reviewed) == 1
    assert reviewer.reviewed[0][0].count("<a href=") >= 7  # все акции в посте
    assert channel.posts == 0  # но в канал ничего не ушло


def test_wiping_memory_brings_back_a_post_with_current_prices(moderated):
    """Стёрли state/seen.json — и следующий прогон снова покажет всю витрину."""
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("seed")

        FakeReviewer.instances.clear()
        _run("run")
        assert FakeReviewer.instances == []  # память полна, говорить не о чем

        (tmp_path / "seen.json").write_text("{}", encoding="utf-8")
        _run("run", allow_empty_state=False)

    assert len(FakeReviewer.instances[-1].reviewed) == 1


def test_repeat_press_is_impossible_after_updates_are_confirmed(moderated):
    """Подтверждённые нажатия Telegram больше не отдаёт.

    Без этого потерянный pending.json означал бы, что одно и то же нажатие
    приходит на каждом прогоне и пост уходит в канал раз в 15 минут сутки.
    """
    config, tmp_path = moderated
    channel = FakeSender()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main_module, "build_senders", lambda *_: [channel])
        _run("run")
        draft_id = FakeReviewer.instances[-1].reviewed[0][2]

        approver = FakeReviewer("t", "c")
        approver.callbacks = [press(draft_id)]
        approver.next_offset = 77
        patch.setattr("avenue_bot.senders.telegram.TelegramSender",
                      lambda *a, **k: approver)
        _run("approvals")

    # Смещение подтверждено до публикации, а не после.
    assert approver.confirmed == 77
    assert channel.posts == 1
