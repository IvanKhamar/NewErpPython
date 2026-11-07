"""The business date is the Kyiv date, whatever zone the machine runs in.

The window: 22:00 to 24:00 UTC in winter and 21:00 to 24:00 UTC in summer, when Kyiv is already on the next day.
`TZ=UTC12 uv run pytest` runs the whole suite on a machine whose date differs from Kyiv for 14 hours a day.
"""

from datetime import UTC, datetime

import pytest

from newerp.application import common
from newerp.infrastructure.marketing.integrations import _timestamp as instagram_time
from newerp.infrastructure.monobank import _when as monobank_when


class _FixedInstant(datetime):
    instant = datetime(2026, 11, 6, 23, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


@pytest.mark.parametrize(
    ("utc", "kyiv"),
    [
        (datetime(2026, 11, 6, 21, 59, tzinfo=UTC), datetime(2026, 11, 6, 23, 59)),  # winter: UTC+2, the same date
        (datetime(2026, 11, 6, 22, 0, tzinfo=UTC), datetime(2026, 11, 7, 0, 0)),  # Kyiv is on the next day, UTC is not
        (datetime(2026, 11, 6, 23, 30, tzinfo=UTC), datetime(2026, 11, 7, 1, 30)),
        (datetime(2026, 11, 7, 0, 0, tzinfo=UTC), datetime(2026, 11, 7, 2, 0)),  # the same date again
        (datetime(2026, 7, 1, 20, 59, tzinfo=UTC), datetime(2026, 7, 1, 23, 59)),  # summer: UTC+3
        (datetime(2026, 7, 1, 21, 0, tzinfo=UTC), datetime(2026, 7, 2, 0, 0)),
    ],
)
def test_the_system_clock_gives_kyiv_time(monkeypatch, utc, kyiv):
    monkeypatch.setattr(_FixedInstant, "instant", utc)
    monkeypatch.setattr(common, "datetime", _FixedInstant)

    assert common.SystemClock().now() == kyiv


def test_a_time_with_a_zone_from_a_service_becomes_kyiv_time():
    assert monobank_when("2026-11-06T23:30:00Z") == datetime(2026, 11, 7, 1, 30)
    assert instagram_time("2026-11-06T23:30:00+0000") == datetime(2026, 11, 7, 1, 30)
