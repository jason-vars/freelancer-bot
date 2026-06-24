from __future__ import annotations
from typing import Any

def safe_get(d: dict[str, Any] | None, key: str, default=None):
    if not d:
        return default
    v = d.get(key, default)
    return v if v is not None else default

def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))
