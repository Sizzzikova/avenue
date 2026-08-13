"""Правило публикации: дайджест выходит только при изменениях."""
from avenue_bot import state
from avenue_bot.models import Car


def car(car_id="6312", day_price=6700, discount_pct=20, city="Новосибирск"):
    return Car(
        city=city,
        car_id=car_id,
        name="Exeed TXL 4WD",
        url="https://avenuerent.ru/autopark/cars/Exeed-TXL-car713/",
        image_url="https://avenuerent.ru/upload/a.webp",
        base_price=8400,
        day_price=day_price,
        old_price=8400,
        discount_pct=discount_pct,
    )


def test_identical_snapshots_do_not_trigger_post():
    first = state.build([car()])
    second = state.build([car()])
    assert not state.changed(first, second)


def test_price_change_triggers_post():
    before = state.build([car(day_price=6700)])
    after = state.build([car(day_price=6000)])
    assert state.changed(before, after)


def test_new_car_triggers_post():
    before = state.build([car()])
    after = state.build([car(), car(car_id="5750")])
    assert state.changed(before, after)


def test_removed_car_triggers_post():
    before = state.build([car(), car(car_id="5750")])
    after = state.build([car()])
    assert state.changed(before, after)


def test_photo_change_alone_does_not_trigger_post():
    """Замена фотографии на сайте не должна порождать лишний пост."""
    before = state.build([car()])
    same_car_new_photo = Car(**{**car().__dict__, "image_url": "https://x/other.webp"})
    assert not state.changed(before, state.build([same_car_new_photo]))


def test_same_id_in_different_cities_is_not_confused():
    before = state.build([car(city="Новосибирск")])
    after = state.build([car(city="Новосибирск"), car(city="Иркутск")])
    assert state.changed(before, after)


class TestPersistence:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "snapshot.json"
        snapshot = state.build([car()])
        state.save(path, snapshot, posted=True)

        loaded = state.load(path)
        assert loaded.fingerprint == snapshot.fingerprint
        assert loaded.posted_at is not None
        assert not state.changed(loaded, snapshot)

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert state.load(tmp_path / "нет.json").is_empty

    def test_corrupted_file_reads_as_empty(self, tmp_path):
        path = tmp_path / "snapshot.json"
        path.write_text("{сломано", encoding="utf-8")
        assert state.load(path).is_empty
