"""Picture limits: at most DAILY_LIMIT pictures a day and MONTHLY_LIMIT a month (Indian dates).

Reads (ADR-120) share the file but have their own counter and daily cap.

Every picture - from the shop system and from the phone app - goes through /generate, so this is
the one place that can cap what Gemini costs. A slot is *reserved* before Gemini is called (the
shop page sends several garments at once, so check-then-call would overshoot) and *given back* if
no picture came out.

The count is one small JSON file per month in Cloud Storage, `usage/<YYYY-MM>.json`, changed with
compare-and-swap on the object's generation, so two service instances can never both take the
last slot. If it cannot be read or written, no picture is made: the cap is the point.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class LimitReached(Exception):
    """The plain sentence staff see."""


class Conflict(Exception):
    """Someone else changed the count between our read and write; read again."""


class Store(Protocol):
    def read(self, month: str) -> tuple[dict, int]:
        """(counts, generation); generation 0 when there is no file yet."""

    def write(self, month: str, counts: dict, generation: int) -> None:
        """Write only if the file is still at `generation`, else raise Conflict."""


@dataclass(frozen=True)
class Usage:
    today: int
    month: int
    daily_limit: int
    monthly_limit: int

    @property
    def left_today(self) -> int:
        return max(0, min(self.daily_limit - self.today, self.monthly_limit - self.month))


def _keys(now: datetime) -> tuple[str, str]:
    local = now.astimezone(IST)
    return f"{local:%Y-%m}", local.date().isoformat()


def _update(store: Store, now: datetime, change: Callable[[dict, str], dict], tries: int = 8) -> dict:
    month, day = _keys(now)
    for _ in range(tries):
        counts, generation = store.read(month)
        # Copy every key, so one counter's change never drops the other's (pictures vs reads).
        new = change(
            {
                **counts,
                "days": dict(counts.get("days", {})),
                "month": counts.get("month", 0),
                "reads": dict(counts.get("reads", {})),
            },
            day,
        )
        try:
            store.write(month, new, generation)
            return new
        except Conflict:
            continue
    raise LimitReached("Anupam AI is very busy right now. Try again in a moment.")


def reserve(store: Store, now: datetime, daily_limit: int, monthly_limit: int) -> None:
    """Take one picture's slot, or raise LimitReached with the sentence to show."""

    def take(c: dict, day: str) -> dict:
        if c["month"] >= monthly_limit:
            raise LimitReached(
                f"This month's {monthly_limit} pictures are used up. More from the 1st."
            )
        if c["days"].get(day, 0) >= daily_limit:
            raise LimitReached(f"Today's {daily_limit} pictures are used up. More tomorrow.")
        c["days"][day] = c["days"].get(day, 0) + 1
        c["month"] += 1
        return c

    _update(store, now, take)


def give_back(store: Store, now: datetime) -> None:
    """No picture came out: return the slot taken by reserve()."""

    def back(c: dict, day: str) -> dict:
        if c["days"].get(day, 0) > 0:
            c["days"][day] -= 1
            c["month"] = max(0, c["month"] - 1)
        return c

    _update(store, now, back)


def reserve_read(store: Store, now: datetime, daily_limit: int) -> None:
    """Take one read's slot (shop system extraction, ADR-120), or raise LimitReached. Reads are
    counted apart from pictures and have no monthly cap: the daily one is what bounds the cost."""

    def take(c: dict, day: str) -> dict:
        if c["reads"].get(day, 0) >= daily_limit:
            raise LimitReached(f"Today's {daily_limit} reads are used up. More tomorrow.")
        c["reads"][day] = c["reads"].get(day, 0) + 1
        return c

    _update(store, now, take)


def give_back_read(store: Store, now: datetime) -> None:
    """No answer came out: return the slot taken by reserve_read()."""

    def back(c: dict, day: str) -> dict:
        if c["reads"].get(day, 0) > 0:
            c["reads"][day] -= 1
        return c

    _update(store, now, back)


@dataclass(frozen=True)
class ReadUsage:
    today: int
    daily_limit: int

    @property
    def left_today(self) -> int:
        return max(0, self.daily_limit - self.today)


def reads_current(store: Store, now: datetime, daily_limit: int) -> ReadUsage:
    month, day = _keys(now)
    counts, _ = store.read(month)
    return ReadUsage(counts.get("reads", {}).get(day, 0), daily_limit)


def current(store: Store, now: datetime, daily_limit: int, monthly_limit: int) -> Usage:
    month, day = _keys(now)
    counts, _ = store.read(month)
    return Usage(counts.get("days", {}).get(day, 0), counts.get("month", 0), daily_limit, monthly_limit)


class GcsStore:
    """The real store: usage/<YYYY-MM>.json in a Cloud Storage bucket."""

    def __init__(self, bucket_name: str, project: str | None = None) -> None:
        from google.cloud import storage

        self._bucket = storage.Client(project=project).bucket(bucket_name)

    def read(self, month: str) -> tuple[dict, int]:
        blob = self._bucket.get_blob(f"usage/{month}.json")
        if blob is None:
            return {}, 0
        return json.loads(blob.download_as_bytes()), int(blob.generation)

    def write(self, month: str, counts: dict, generation: int) -> None:
        from google.api_core.exceptions import PreconditionFailed

        try:
            self._bucket.blob(f"usage/{month}.json").upload_from_string(
                json.dumps(counts), content_type="application/json", if_generation_match=generation
            )
        except PreconditionFailed as exc:
            raise Conflict() from exc


def today_in_india(now: datetime) -> date:
    return now.astimezone(IST).date()
