from __future__ import annotations

import time
from typing import Any

def safe_get(d: dict[str, Any] | None, key: str, default=None):
    if not d:
        return default
    v = d.get(key, default)
    return v if v is not None else default

def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def _parse_hhmm(value: str | None) -> int | None:
    """Parse an "HH:MM" (or "HH") string to minutes-since-midnight, or None."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        parts = s.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def within_active_hours(start: str | None, end: str | None, now_minutes: int | None = None) -> bool:
    """Is the current local time inside the [start, end) window?

    Both bounds are "HH:MM" in the machine's local timezone. If either is empty or
    unparseable the window is treated as DISABLED (always active). Windows that
    wrap past midnight (e.g. 22:00–06:00) are handled. start == end means 24h.
    """
    a = _parse_hhmm(start)
    b = _parse_hhmm(end)
    if a is None or b is None:
        return True
    if now_minutes is None:
        lt = time.localtime()
        now_minutes = lt.tm_hour * 60 + lt.tm_min
    if a == b:
        return True
    if a < b:
        return a <= now_minutes < b
    return now_minutes >= a or now_minutes < b
