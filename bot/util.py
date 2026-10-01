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


def current_minutes(tz_offset_hours: float | None = None) -> int:
    """Minutes-since-midnight for the active-hours check.

    With ``tz_offset_hours`` None, uses the MACHINE's local time (time.localtime).
    When set (e.g. 9, -5, 5.5), uses UTC + that offset instead — so the window is
    evaluated in YOUR timezone even when the bot runs on a server set to UTC."""
    if tz_offset_hours is None:
        lt = time.localtime()
        return lt.tm_hour * 60 + lt.tm_min
    u = time.gmtime()
    return (u.tm_hour * 60 + u.tm_min + round(tz_offset_hours * 60)) % (24 * 60)


def fmt_minutes(total: int) -> str:
    """Render minutes-since-midnight as HH:MM (for diagnostics)."""
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def within_active_hours(
    start: str | None,
    end: str | None,
    now_minutes: int | None = None,
    tz_offset_hours: float | None = None,
) -> bool:
    """Is the current time inside the [start, end) window?

    Bounds are "HH:MM". The "current" time is the machine's local time, unless
    ``tz_offset_hours`` is given (then UTC + offset). If either bound is empty or
    unparseable the window is DISABLED (always active). Windows that wrap past
    midnight (e.g. 22:00–06:00) are handled. start == end means 24h.
    """
    a = _parse_hhmm(start)
    b = _parse_hhmm(end)
    if a is None or b is None:
        return True
    if now_minutes is None:
        now_minutes = current_minutes(tz_offset_hours)
    if a == b:
        return True
    if a < b:
        return a <= now_minutes < b
    return now_minutes >= a or now_minutes < b
