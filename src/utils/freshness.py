"""
Converts messy human-readable relative timestamps scraped from news/job sites
("2 hours ago", "1 day ago", "Just now", "Sep 9", "2025-09-09T10:00:00Z" ...)
into proper ISO-8601 UTC strings, and tells you whether a row falls inside the
last N hours (the assignment's 24-hour freshness requirement).
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from dateutil import parser as dateutil_parser

_RELATIVE_PATTERN = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>second|minute|hour|day|week|month|year)s?\s*ago",
    re.IGNORECASE,
)

_UNIT_TO_KWARG = {
    "second": "seconds",
    "minute": "minutes",
    "hour": "hours",
    "day": "days",
    "week": "weeks",
}


def parse_relative_or_absolute(raw_text: str, now: Optional[datetime] = None) -> Optional[str]:
    """
    Returns an ISO-8601 UTC string, or None if the text cannot be parsed at all.
    Handles:
      - "5 minutes ago", "2 hours ago", "1 day ago", "3 weeks ago"
      - "just now" / "moments ago"
      - "Yesterday"
      - absolute strings dateutil can parse ("Sep 9, 2026", "2026-09-09T08:00:00Z")
      - month/year-ago cases are approximated (30 days/365 days) since exact
        calendar math rarely matters once something is >1 week old (it will
        fail the 24h freshness filter anyway).
    """
    if not raw_text:
        return None
    now = now or datetime.now(timezone.utc)
    text = raw_text.strip().lower()

    if text in {"just now", "moments ago", "now"}:
        return now.isoformat()

    if text == "yesterday":
        return (now - timedelta(days=1)).isoformat()

    match = _RELATIVE_PATTERN.search(text)
    if match:
        num = int(match.group("num"))
        unit = match.group("unit")
        if unit in _UNIT_TO_KWARG:
            delta = timedelta(**{_UNIT_TO_KWARG[unit]: num})
        elif unit == "month":
            delta = timedelta(days=30 * num)
        elif unit == "year":
            delta = timedelta(days=365 * num)
        else:
            return None
        return (now - delta).isoformat()

    # Fall back to absolute-date parsing
    try:
        dt = dateutil_parser.parse(raw_text, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, OverflowError):
        return None


def is_within_freshness_window(iso_timestamp: str, hours: int = 24, now: Optional[datetime] = None) -> bool:
    """True if iso_timestamp is within the last `hours` hours of `now`."""
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) <= timedelta(hours=hours) and dt <= now + timedelta(minutes=5)
