"""Точка входа: python -m avenue_bot.main --mode run|seed|dry-run.

Режимы:

* dry-run — скачать, разобрать и напечатать посты в консоль. Ничего не шлёт
  и не трогает состояние. Значение по умолчанию, чтобы случайный запуск не
  вывалил посты в канал.
* seed    — записать все текущие акции как «уже виденные», ничего не публикуя.
  Запускается один раз перед включением расписания, иначе первый прогон
  опубликует сразу все действующие акции.
* run     — боевой режим. Если согласование включено, пост уходит в рабочий
  чат с кнопками; если выключено — сразу в канал.
* approvals — разобрать нажатия кнопок под постами, ждущими согласования.
  Сайт не трогает, работает только с черновиками. Запускается чаще основного
  режима, потому что своего сервера у бота нет и нажатия он забирает опросом.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass

from .alerts import Alerter
from .config import City, Config, Secrets, load_config
from .fetch import FetchError, Fetcher
from .models import Promo
from .parse import parse_promos
from .prices import enrich_prices
from .drafts import APPROVED, PENDING, REJECTED, Drafts
from .render import (
    render_digest_max,
    render_digest_telegram,
    render_max,
    render_telegram,
)
from .state import State

log = logging.getLogger("avenue_bot")


@dataclass
class CityResult:
    city: City
    promos: list[Promo]
    ok: bool


def collect(config: Config, cities: list[City], alerter: Alerter) -> list[CityResult]:
    """Скачать и разобрать страницы акций всех городов."""
    results: list[CityResult] = []
    with Fetcher(config.http) as fetcher:
        for city in cities:
            url = city.promos_url
            try:
                html = fetcher.get_text(url)
            except FetchError as error:
                alerter.notify(f"{city.name}: страница акций недоступна.\n{url}\n{error}")
                results.append(CityResult(city, [], ok=False))
                continue

            if not html.strip():
                alerter.notify(f"{city.name}: сайт вернул пустой ответ.\n{url}")
                results.append(CityResult(city, [], ok=False))
                continue

            promos = parse_promos(html, city, config.selectors)

            # Защита от тихой поломки: страница пришла, тело непустое, а карточек
            # ноль — это почти наверняка переверстали сайт, а не «акций нет».
            # Состояние в этом случае не трогаем и падаем красным.
            if not promos:
                alerter.notify(
                    f"{city.name}: на странице акций не найдено ни одной карточки, "
                    f"хотя страница загрузилась ({len(html)} байт). "
                    f"Похоже, изменилась вёрстка — проверьте селекторы в config.yml.\n{url}"
                )
                results.append(CityResult(city, [], ok=False))
                continue

            enrich_prices(promos, city, fetcher, config.prices)
            log.info("%s: акций разобрано — %s", city.name, len(promos))
            results.append(CityResult(city, promos, ok=True))
    return results


def build_senders(config: Config, secrets: Secrets) -> list:
    """Собрать включённые отправители. Несконфигурированный — просто пропускается."""
    senders = []
    messengers = config.messengers

    if messengers.get("telegram", {}).get("enabled", True):
        if secrets.telegram_bot_token and secrets.telegram_chat_id:
            from .senders.telegram import TelegramSender

            senders.append(
                TelegramSender(secrets.telegram_bot_token, secrets.telegram_chat_id)
            )
        else:
            log.warning("Telegram включён в конфиге, но нет токена или chat_id — пропускаю")

    if messengers.get("max", {}).get("enabled", False):
        if secrets.max_bot_token and secrets.max_chat_id:
            from .senders.max_messenger import MaxSender

            senders.append(
                MaxSender(
                    secrets.max_bot_token,
                    secrets.max_chat_id,
                    api_url=messengers["max"].get("api_url", "https://platform-api.max.ru"),
                )
            )
        else:
            log.warning("MAX включён в конфиге, но нет токена или chat_id — пропускаю")

    return senders


def split_promos(
    promos: list[Promo],
    state: State,
    include_changes: bool,
    price_drop_percent: float = 5.0,
) -> tuple[list[Promo], list[Promo]]:
    """Разделить акции на впервые увиденные и заметно подешевевшие.

    Всё остальное в результат не попадает, поэтому в спокойные сутки обе
    пачки пустые и пост не выходит.

    Про цены. Их отдаёт живой калькулятор сайта, и они гуляют сами по себе:
    от дат расчёта, от сезона, а когда калькулятор не ответил — подставляется
    базовая цена из вёрстки, то есть просто другое число. Поэтому снижением
    считается только то, которое:

    * посчитано по двум ценам из одного источника (сравнивать цену из
      калькулятора с ценой из вёрстки нельзя — это разные методики);
    * превышает порог price_drop_percent — иначе копеечное колебание каждый
      день выдавало бы «цены снизились».
    """
    new: list[Promo] = []
    changed: list[Promo] = []

    for promo in promos:
        if state.is_new(promo):
            new.append(promo)
            continue

        if state.is_changed(promo):
            # Поправили название, плашку или сроки — запоминаем, но молчим.
            log.info("Изменилось описание, в канал не идёт: %s", promo.title)
            state.mark_seen(promo, posted=False)

        if not include_changes or promo.price is None:
            continue

        baseline = state.baseline_price(promo)
        if baseline is None:
            # Сравнивать не с чем: цены раньше не знали или знали из другого
            # источника. Запоминаем текущую и ждём следующего прогона.
            state.update_baseline_price(promo)
            continue

        if promo.price >= baseline:
            # Подорожание — не новость, и точку отсчёта оно не двигает.
            # Точка отсчёта — это цена, которую мы в последний раз назвали в канале.
            # Если сдвинуть её вверх молча, то возврат к прежней цене бот потом
            # объявит скидкой, хотя подписчикам про подорожание никто не говорил.
            state.clear_pending_drop(promo)
            continue

        drop = (baseline - promo.price) / baseline * 100
        if drop < price_drop_percent:
            # Мелкое колебание. Молчим и точку отсчёта НЕ двигаем, чтобы
            # накопившееся снижение всё-таки заметить.
            log.debug(
                "Снижение %.1f%% меньше порога %.1f%%: %s",
                drop,
                price_drop_percent,
                promo.title,
            )
            state.clear_pending_drop(promo)
            continue

        # Снижение за порогом — но ждём подтверждения на следующем прогоне.
        # Калькулятор сайта иногда выдаёт разовый провал, и без этой проверки
        # он превращался бы в пост «цены снизились», после которого цена
        # назавтра возвращается обратно.
        if state.pending_drop(promo) is None:
            log.info(
                "Снижение %.1f%% у «%s» — жду подтверждения на следующем прогоне",
                drop,
                promo.title,
            )
            state.set_pending_drop(promo, promo.price)
            continue

        promo.previous_price = baseline
        changed.append(promo)

    return new, changed


def _delay_for(sender, config: Config) -> float:
    return float(config.messengers.get(sender.name, {}).get("delay_seconds", 1.0))


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    secrets = Secrets.from_env()
    alerter = Alerter(secrets.telegram_bot_token, secrets.telegram_admin_chat_id)

    if args.mode == "approvals":
        # Сайт не трогаем: всё нужное уже лежит в черновиках.
        try:
            return run_approvals(config, secrets, alerter)
        finally:
            alerter.close()

    cities = config.cities
    if args.city:
        cities = [city for city in cities if city.key in args.city]
        if not cities:
            log.error("Города %s нет в конфиге", ", ".join(args.city))
            return 2

    results = collect(config, cities, alerter)
    failed = [result.city.name for result in results if not result.ok]
    ok_results = [result for result in results if result.ok]

    state = State.load(config.state_path)
    exit_code = 1 if failed else 0

    if args.mode == "seed":
        total = 0
        for result in ok_results:
            for promo in result.promos:
                state.mark_seen(promo, posted=False)
                total += 1
        state.save()
        log.info("Seed: записано акций как виденные — %s", total)
        alerter.close()
        return exit_code

    all_promos = [promo for result in ok_results for promo in result.promos]

    # Состояние пустое, а мы в боевом режиме — значит seed не делали или
    # state/seen.json потерялся (например, workflow не смог его закоммитить).
    # Публиковать нельзя: в канал уйдут разом все действующие акции.
    if args.mode == "run" and not state.data and all_promos and not args.allow_empty_state:
        alerter.notify(
            f"Состояние пустое ({config.state_path}), а на сайте {len(all_promos)} акций. "
            "Публикация остановлена, иначе в канал ушли бы все сразу.\n\n"
            "ЧТО СДЕЛАТЬ: запустить workflow «Акции Авеню» в режиме seed "
            "(Actions → Run workflow → mode: seed). Он запомнит текущие акции, "
            "ничего не публикуя, и дальше всё пойдёт само.\n\n"
            "Если seed уже делали, значит в прошлый раз не сохранился "
            "state/seen.json — посмотрите шаг «Сохранить состояние» в последнем "
            "прогоне Actions.\n\n"
            "Опубликовать все акции разом можно намеренно: режим run с галочкой "
            "«Разрешить работу при пустом состоянии» (в командной строке — "
            "флаг --allow-empty-state)."
        )
        alerter.close()
        return 1

    state.migrate_keys(all_promos)
    new, changed = split_promos(
        all_promos, state, config.include_changes, config.price_drop_percent
    )

    # Состояние не пустое, но новыми оказались вообще все акции — так не бывает.
    # Обычно это значит, что на сайте поменялись адреса или id автомобилей и
    # бот перестал узнавать старые записи. Публиковать нельзя: получится залп.
    if (
        args.mode == "run"
        and state.data
        and len(all_promos) > 1
        and len(new) == len(all_promos)
        and not args.allow_empty_state
    ):
        alerter.notify(
            f"Новыми оказались сразу все {len(all_promos)} акций, хотя состояние "
            "не пустое. Публикация остановлена, иначе в канал ушёл бы залп.\n\n"
            "Похоже, на сайте изменились адреса или id автомобилей, и бот перестал "
            "узнавать сохранённые записи.\n\n"
            "ЧТО СДЕЛАТЬ: посмотреть страницу акций и прогнать workflow в режиме "
            "seed заново (Actions → Run workflow → mode: seed).\n\n"
            "Если обновился действительно весь автопарк и опубликовать нужно всё, "
            "запустите режим run с галочкой «Разрешить работу при пустом состоянии»."
        )
        alerter.close()
        return 1

    if args.mode == "dry-run":
        _print_dry_run(config, new, changed, len(all_promos))
        alerter.close()
        return exit_code

    if not new and not changed:
        # Спокойные сутки: на сайте всё по-прежнему, в канал ничего не уходит.
        log.info("Изменений нет — пост не выходит (акций на сайте: %s)", len(all_promos))
        _finish_state(state, ok_results)
        alerter.close()
        return exit_code

    senders = build_senders(config, secrets)
    if not senders:
        alerter.notify("Нет ни одного настроенного мессенджера — публиковать некуда.")
        alerter.close()
        return 1

    posts = build_posts(new, changed, config)

    if config.moderation_enabled:
        sent, send_failed = _send_for_review(posts, config, secrets, alerter)
    else:
        sent, send_failed = _publish(posts, senders, config, alerter)

    # Помечаем виденными в любом случае: если пост не ушёл, следующий прогон
    # не должен долбить тем же самым — про сбой уже есть алерт в служебном чате.
    # Отправленное на согласование тоже помечаем: иначе завтрашний прогон
    # приготовил бы второй такой же черновик.
    for promo in new + changed:
        state.mark_seen(promo, posted=sent > 0)

    _finish_state(state, ok_results)
    for sender in senders:
        close = getattr(sender, "close", None)
        if close:
            close()

    log.info(
        "Готово. Новых акций: %s, подешевевших: %s, постов %s: %s",
        len(new),
        len(changed),
        "на согласование" if config.moderation_enabled else "опубликовано",
        sent,
    )
    if send_failed:
        exit_code = 1
    alerter.close()
    return exit_code


def _finish_state(state: State, ok_results: list[CityResult]) -> None:
    """Почистить пропавшие акции и сохранить состояние."""
    removed = state.prune(
        # Пропавшие акции чистим только по успешно обработанным городам.
        live_keys={promo.key for result in ok_results for promo in result.promos},
        city_keys={result.city.key for result in ok_results},
    )
    if removed:
        log.info("Снято с публикации (пропали с сайта): %s", len(removed))
    state.save()


def build_posts(
    new: list[Promo], changed: list[Promo], config: Config
) -> list[tuple[str, str, str | None]]:
    """Собрать готовые тексты постов: (для Telegram, для MAX, ссылка на фото).

    Текст готовится один раз и дальше только пересылается. Это важно для
    согласования: в канал уходит ровно то, что человек видел под кнопками,
    даже если цены на сайте к этому моменту успели измениться.
    """
    items = new + changed

    if config.post_mode == "separate":
        return [(render_telegram(p), render_max(p), p.image_url) for p in items]

    if len(items) == 1:
        # Дайджест из одной строки выглядел бы бедно — шлём обычный пост с фото.
        single = items[0]
        return [(render_telegram(single), render_max(single), single.image_url)]

    return [
        (
            render_digest_telegram(new, changed),
            render_digest_max(new, changed),
            None,
        )
    ]


def _publish(
    posts: list[tuple[str, str, str | None]],
    senders: list,
    config: Config,
    alerter: Alerter,
) -> tuple[int, bool]:
    """Отправить готовые посты в каналы."""
    sent = 0
    failed = False

    for telegram_text, max_text, photo_url in posts:
        for sender in senders:
            text = telegram_text if sender.name == "telegram" else max_text
            try:
                sender.send_prepared(text, photo_url)
                sent += 1
                log.info("[%s] опубликовано", sender.name)
            except Exception as error:  # noqa: BLE001 — один канал не роняет другой
                failed = True
                alerter.notify(f"{sender.name}: не удалось опубликовать пост.\n{error}")
            time.sleep(_delay_for(sender, config))

    return sent, failed


def _send_for_review(
    posts: list[tuple[str, str, str | None]],
    config: Config,
    secrets: Secrets,
    alerter: Alerter,
) -> tuple[int, bool]:
    """Отправить посты в чат согласования и запомнить их как черновики."""
    if not secrets.telegram_admin_chat_id:
        # Алерт сюда же не уйдёт — чата-то и нет, но в логе прогона будет видно.
        alerter.notify(
            "Включено согласование, но не задан рабочий чат: нужен секрет "
            "TELEGRAM_ADMIN_CHAT_ID."
        )
        return 0, True

    from .senders.telegram import TelegramSender

    reviewer = TelegramSender(secrets.telegram_bot_token, secrets.telegram_chat_id)
    drafts = Drafts.load(config.pending_path)
    sent = 0
    failed = False

    try:
        for telegram_text, max_text, photo_url in posts:
            draft_id = drafts.add(telegram_text, max_text, photo_url)
            try:
                message_id = reviewer.send_for_review(
                    secrets.telegram_admin_chat_id, telegram_text, photo_url, draft_id
                )
                drafts.set_review_message(draft_id, message_id)
                sent += 1
                log.info("Пост отправлен на согласование, черновик %s", draft_id)
            except Exception as error:  # noqa: BLE001
                failed = True
                # Черновик, который никто не увидит, согласовать нечем — убираем.
                drafts.drafts.pop(draft_id, None)
                alerter.notify(f"Не удалось отправить пост на согласование.\n{error}")
            time.sleep(_delay_for(reviewer, config))
        drafts.forget_decided()
        drafts.save()
    finally:
        reviewer.close()

    return sent, failed


def run_approvals(config: Config, secrets: Secrets, alerter: Alerter) -> int:
    """Разобрать нажатия кнопок под постами, ждущими согласования.

    Отдельный режим, который запускается чаще основного. Сайт при этом не
    трогается вообще: всё нужное уже лежит в черновиках.
    """
    if not config.moderation_enabled:
        log.info("Согласование выключено — разбирать нечего")
        return 0
    if not secrets.telegram_bot_token or not secrets.telegram_admin_chat_id:
        log.warning("Нет токена или рабочего чата — согласовывать негде, пропускаю")
        return 0

    from .senders.telegram import TelegramSender

    drafts = Drafts.load(config.pending_path)
    reviewer = TelegramSender(secrets.telegram_bot_token, secrets.telegram_chat_id)
    senders = build_senders(config, secrets)
    exit_code = 0

    try:
        for draft_id in drafts.expire_old(config.moderation_expire_hours):
            draft = drafts.get(draft_id) or {}
            log.info("Черновик %s просрочен и больше не согласуется", draft_id)
            if draft.get("review_message_id"):
                reviewer.finish_review(
                    secrets.telegram_admin_chat_id,
                    draft["review_message_id"],
                    draft.get("kind", "text"),
                    draft.get("telegram_text", ""),
                    "⌛️ Просрочено, пост не опубликован",
                )

        try:
            callbacks, next_offset = reviewer.get_callbacks(drafts.update_offset)
        except Exception as error:  # noqa: BLE001
            alerter.notify(f"Не удалось прочитать нажатия кнопок.\n{error}")
            drafts.save()
            return 1

        for callback in callbacks:
            if not _handle_callback(
                callback, drafts, reviewer, senders, config, secrets, alerter
            ):
                exit_code = 1

        # Смещение двигаем только после разбора: иначе при падении посередине
        # нажатия потерялись бы навсегда.
        drafts.update_offset = next_offset
        drafts.forget_decided()
        drafts.save()
    finally:
        reviewer.close()
        for sender in senders:
            close = getattr(sender, "close", None)
            if close:
                close()

    return exit_code


def _handle_callback(
    callback: dict,
    drafts: Drafts,
    reviewer,
    senders: list,
    config: Config,
    secrets: Secrets,
    alerter: Alerter,
) -> bool:
    """Обработать одно нажатие. False — публикация сорвалась."""
    callback_id = callback.get("id", "")
    action, _, draft_id = callback.get("data", "").partition(":")

    if action not in {"pub", "rej"} or not draft_id:
        reviewer.answer_callback(callback_id, "Непонятная кнопка")
        return True

    draft = drafts.get(draft_id)
    if draft is None:
        reviewer.answer_callback(callback_id, "Черновик не найден — он уже устарел")
        return True
    if draft.get("status") != PENDING:
        reviewer.answer_callback(callback_id, "По этому посту решение уже принято")
        return True

    if action == "rej":
        drafts.set_status(draft_id, REJECTED)
        reviewer.finish_review(
            secrets.telegram_admin_chat_id,
            draft.get("review_message_id"),
            draft.get("kind", "text"),
            draft.get("telegram_text", ""),
            "🚫 Отклонено, в канал не ушло",
        )
        reviewer.answer_callback(callback_id, "Не публикуем")
        log.info("Черновик %s отклонён", draft_id)
        return True

    ok = True
    for sender in senders:
        text = draft["telegram_text"] if sender.name == "telegram" else draft["max_text"]
        try:
            sender.send_prepared(text, draft.get("photo_url"))
            log.info("[%s] опубликован согласованный черновик %s", sender.name, draft_id)
        except Exception as error:  # noqa: BLE001
            ok = False
            alerter.notify(f"{sender.name}: не удалось опубликовать пост.\n{error}")
        time.sleep(_delay_for(sender, config))

    if ok:
        drafts.set_status(draft_id, APPROVED)
        reviewer.finish_review(
            secrets.telegram_admin_chat_id,
            draft.get("review_message_id"),
            draft.get("kind", "text"),
            draft.get("telegram_text", ""),
            "✅ Опубликовано",
        )
        reviewer.answer_callback(callback_id, "Опубликовано")
    else:
        # Статус не трогаем: кнопка останется рабочей, можно нажать ещё раз.
        reviewer.answer_callback(callback_id, "Не получилось опубликовать, см. алерты")

    return ok


def _print_dry_run(
    config: Config, new: list[Promo], changed: list[Promo], total: int
) -> None:
    """Показать в консоли, что ушло бы в канал."""
    if not new and not changed:
        print(f"\nИзменений нет — пост не вышел бы. Акций на сайте: {total}.")
        return

    if config.post_mode == "digest" and len(new) + len(changed) > 1:
        print("\n===== дайджест (один пост) =====")
        print(render_digest_telegram(new, changed))
    else:
        for promo in new + changed:
            label = "подешевело" if promo.previous_price else "новая акция"
            print(f"\n===== {promo.city_name} · {label} =====")
            print(render_telegram(promo))

    print(
        f"\nИтого: новых {len(new)}, подешевевших {len(changed)}, "
        f"акций на сайте {total}."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Автопостинг акций «Авеню»")
    parser.add_argument(
        "--mode",
        choices=["run", "seed", "dry-run", "approvals"],
        default="dry-run",
        help="run — подготовить пост, seed — только запомнить текущие, "
        "dry-run — печать в консоль, approvals — разобрать нажатия кнопок согласования",
    )
    parser.add_argument("--config", default=None, help="путь к config.yml")
    parser.add_argument(
        "--city",
        action="append",
        help="ограничить одним городом (ключ из config.yml), можно повторять",
    )
    parser.add_argument(
        "--allow-empty-state",
        action="store_true",
        help="разрешить публикацию при пустом state/seen.json (иначе в канал уйдут "
        "разом все действующие акции — обычно это признак потерянного состояния)",
    )
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
