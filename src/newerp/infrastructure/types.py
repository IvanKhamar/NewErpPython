"""Column types shared by the mappings."""

from datetime import time, timedelta

from sqlalchemy import Time
from sqlalchemy.types import TypeDecorator


class TimeOfDay(TypeDecorator):
    """MySQL TIME as datetime.time. The MySQL driver returns TIME as a timedelta (it may exceed 24 h)."""

    impl = Time
    cache_ok = True

    def process_bind_param(self, value: time | None, dialect) -> time | None:
        return value

    def process_result_value(self, value: timedelta | time | None, dialect) -> time | None:
        if isinstance(value, timedelta):
            seconds = int(value.total_seconds()) % (24 * 3600)
            return time(seconds // 3600, seconds % 3600 // 60, seconds % 60)
        return value
