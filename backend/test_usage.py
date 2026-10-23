"""The picture limits (usage.py): run with `python -m pytest backend/test_usage.py`."""

from datetime import UTC, datetime

import pytest

from usage import (
    Conflict,
    LimitReached,
    current,
    give_back,
    give_back_read,
    reads_current,
    reserve,
    reserve_read,
)

NOW = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)  # 1:30 PM in India


class MemoryStore:
    def __init__(self, conflicts: int = 0) -> None:
        self.files: dict[str, tuple[dict, int]] = {}
        self.conflicts = conflicts

    def read(self, month: str) -> tuple[dict, int]:
        return self.files.get(month, ({}, 0))

    def write(self, month: str, counts: dict, generation: int) -> None:
        if self.conflicts:
            self.conflicts -= 1
            raise Conflict()
        if self.files.get(month, ({}, 0))[1] != generation:
            raise Conflict()
        self.files[month] = (counts, generation + 1)


def test_twenty_a_day_then_a_plain_sentence() -> None:
    store = MemoryStore()
    for _ in range(20):
        reserve(store, NOW, 20, 100)
    with pytest.raises(LimitReached, match="Today's 20 pictures are used up"):
        reserve(store, NOW, 20, 100)
    assert current(store, NOW, 20, 100).left_today == 0


def test_the_month_cap_wins_over_a_fresh_day() -> None:
    store = MemoryStore()
    store.files["2026-09"] = ({"days": {"2026-09-22": 20}, "month": 100}, 1)
    with pytest.raises(LimitReached, match="This month's 100 pictures"):
        reserve(store, NOW, 20, 100)


def test_a_failed_picture_gives_its_slot_back() -> None:
    store = MemoryStore()
    reserve(store, NOW, 20, 100)
    give_back(store, NOW)
    usage = current(store, NOW, 20, 100)
    assert (usage.today, usage.month, usage.left_today) == (0, 0, 20)


def test_a_new_month_starts_from_zero() -> None:
    store = MemoryStore()
    store.files["2026-08"] = ({"days": {"2026-08-31": 20}, "month": 100}, 1)
    reserve(store, NOW, 20, 100)
    assert current(store, NOW, 20, 100).month == 1


def test_days_follow_indian_time() -> None:
    store = MemoryStore()
    late_utc = datetime(2026, 9, 23, 19, 0, tzinfo=UTC)  # 00:30 on the 24th in India
    reserve(store, late_utc, 20, 100)
    assert store.files["2026-09"][0]["days"] == {"2026-09-24": 1}


def test_someone_else_writing_first_is_retried_not_lost() -> None:
    store = MemoryStore(conflicts=3)
    reserve(store, NOW, 20, 100)
    assert current(store, NOW, 20, 100).today == 1


def test_left_today_is_the_smaller_of_day_and_month() -> None:
    store = MemoryStore()
    store.files["2026-09"] = ({"days": {"2026-09-23": 2}, "month": 95}, 1)
    assert current(store, NOW, 20, 100).left_today == 5


# --- reads: the shop system's extraction calls, counted apart from pictures (ADR-120) ---


def test_fifty_reads_a_day_then_a_plain_sentence() -> None:
    store = MemoryStore()
    for _ in range(50):
        reserve_read(store, NOW, 50)
    with pytest.raises(LimitReached, match="Today's 50 reads are used up"):
        reserve_read(store, NOW, 50)
    assert reads_current(store, NOW, 50).left_today == 0


def test_reads_and_pictures_do_not_use_each_others_slots() -> None:
    store = MemoryStore()
    for _ in range(20):
        reserve(store, NOW, 20, 100)
    reserve_read(store, NOW, 50)  # pictures are used up; a read still goes through
    for _ in range(49):
        reserve_read(store, NOW, 50)
    reserve(store, NOW, 21, 100)  # reads are used up; a picture still goes through
    assert current(store, NOW, 21, 100).today == 21
    assert reads_current(store, NOW, 50).today == 50


def test_a_picture_giving_its_slot_back_keeps_the_reads() -> None:
    store = MemoryStore()
    reserve_read(store, NOW, 50)
    reserve(store, NOW, 20, 100)
    give_back(store, NOW)
    assert reads_current(store, NOW, 50).today == 1


def test_a_failed_read_gives_its_slot_back() -> None:
    store = MemoryStore()
    reserve_read(store, NOW, 50)
    give_back_read(store, NOW)
    assert reads_current(store, NOW, 50).left_today == 50


def test_reads_follow_indian_time() -> None:
    store = MemoryStore()
    reserve_read(store, datetime(2026, 9, 23, 19, 0, tzinfo=UTC), 50)  # 00:30 on the 24th
    assert store.files["2026-09"][0]["reads"] == {"2026-09-24": 1}
