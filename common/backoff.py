"""Exponential backoff with full jitter, and Retry-After parsing."""

from collections.abc import Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from random import Random


def delays(base: float = 0.5, cap: float = 30.0, rng: Random | None = None) -> Iterator[float]:
    """Yield sleep durations: uniform(0, min(cap, base * 2**n)) for n = 0, 1, 2, ..."""
    rng = rng or Random()
    ceiling = base
    while True:
        yield rng.uniform(0, min(cap, ceiling))
        ceiling = min(cap, ceiling * 2)  # stop growing at the cap, so a long outage never overflows


def retry_after_seconds(header: str | None, now: datetime) -> float | None:
    """Parse a Retry-After header given as delta-seconds or an HTTP-date. None if absent or invalid."""
    if not header:
        return None
    header = header.strip()
    if header.isdigit():
        return float(header)
    try:
        when = parsedate_to_datetime(header)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - now).total_seconds())
