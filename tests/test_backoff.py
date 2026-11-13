from datetime import UTC, datetime

from common.backoff import retry_after_seconds


def test_retry_after_date_without_timezone_is_treated_as_utc():
    now = datetime(1994, 11, 6, 8, 49, 7, tzinfo=UTC)

    result = retry_after_seconds("Sun Nov  6 08:49:37 1994", now)

    assert result == 30.0
