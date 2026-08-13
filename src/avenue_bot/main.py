"""Точка входа.

Режимы:
  run      — собрать данные и, если что-то изменилось, опубликовать дайджест
  dry-run  — то же, но вместо отправки печатает пост в консоль
  seed     — записать текущее состояние без публикации (первый запуск)
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from . import alerts, config as config_module, fetch, parse, render, state
from .senders import max as max_sender, telegram

log = logging.getLogger("avenue_bot")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _plain(text: str) -> str:
    """Убрать HTML-теги, чтобы dry-run читался в консоли как обычный текст."""
    return re.sub(r"<[^>]+>", "", text)


def run(mode: str, state_path: Path) -> int:
    cfg = config_module.load()
    previous = state.load(state_path)

    with fetch.make_client() as client:
        try:
            cars = parse.collect(client, cfg)
        except parse.ParseError as exc:
            alerts.notify(cfg, f"Парсер не нашёл карточки на сайте.\n\n{exc}")
            return 1
        except fetch.FetchError as exc:
            alerts.notify(cfg, f"Сайт недоступен.\n\n{exc}")
            return 1

        log.info("машин со скидкой: %d", len(cars))
        current = state.build(cars)

        if mode == "seed":
            state.save(state_path, current, posted=False)
            log.info("состояние записано без публикации (%d машин)", len(cars))
            return 0

        if not state.changed(previous, current):
            log.info("изменений с прошлого запуска нет — ничего не публикуем")
            return 0

        posts = render.build_digest(cars, previous, cfg)
        if not posts:
            log.info("публиковать нечего: машин со скидкой не осталось")
            state.save(state_path, current, posted=False)
            return 0

        if mode == "dry-run":
            for index, post in enumerate(posts, 1):
                print(f"\n=== сообщение {index}/{len(posts)}, фото: {len(post.images)} ===")
                print(_plain(post.caption))
            log.info("dry-run: ничего не отправлено, состояние не изменено")
            return 0

        if cfg.telegram_ready:
            try:
                telegram.send(client, cfg, posts)
            except telegram.TelegramError as exc:
                alerts.notify(cfg, f"Не удалось опубликовать дайджест.\n\n{exc}")
                return 1
        else:
            log.warning("Telegram не настроен (нет токена или chat_id) — пропускаю")

        if cfg.max_enabled:
            max_sender.send(client, cfg, posts)

        state.save(state_path, current, posted=True)
        log.info("дайджест опубликован, состояние обновлено")
        return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="Бот акций «Авеню»")
    parser.add_argument(
        "--mode",
        choices=("run", "dry-run", "seed"),
        default="dry-run",
        help="dry-run по умолчанию, чтобы случайный запуск ничего не отправил",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=config_module.DEFAULT_STATE,
        help="путь к файлу состояния",
    )
    args = parser.parse_args(argv)
    return run(args.mode, args.state)


if __name__ == "__main__":
    sys.exit(main())
