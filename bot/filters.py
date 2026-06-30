from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from freelancersdk.resources.users import users as fln_users

from .util import safe_get


def _to_epoch_seconds(value: Any) -> float | None:
    """Best-effort convert a project ``created_at``/``time_submitted`` to epoch seconds.

    Accepts epoch ints/floats (e.g. ``1780243435``), numeric strings, or ISO-8601
    strings (with optional trailing ``Z``). Returns ``None`` if it can't be parsed.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Field extraction helpers (shared by polling + webhook paths)
# ---------------------------------------------------------------------------
def extract_country_code(country: Any) -> str | None:
    if isinstance(country, str):
        c = country.strip()
        return c.upper() if len(c) <= 3 else None
    if isinstance(country, dict):
        for key in ("code", "country_code", "iso2", "iso3"):
            v = country.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()
    return None


def extract_country_name(country: Any) -> str | None:
    if isinstance(country, str):
        c = country.strip()
        return c if c else None
    if isinstance(country, dict):
        for key in ("name", "country_name"):
            v = country.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def extract_completed_jobs_count(user: dict[str, Any] | None) -> int | None:
    if not isinstance(user, dict):
        return None

    # Try common locations first.
    candidates = [
        safe_get(safe_get(user, "employer_reputation", {}), "entire_history"),
        safe_get(user, "employer_reputation"),
        safe_get(user, "reputation"),
    ]
    keys = (
        "completed_jobs",
        "complete_jobs",
        "jobs_completed",
        "completed_projects",
        "projects_completed",
        "complete",
        "completed",
    )
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        for k in keys:
            v = obj.get(k)
            if isinstance(v, (int, float)):
                return int(v)

    # Fallback: recursive search for a likely count field.
    def walk(node: Any, depth: int = 0) -> int | None:
        if depth > 5:
            return None
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).lower()
                if ("complete" in key or "completed" in key) and ("job" in key or "project" in key):
                    if isinstance(v, (int, float)):
                        return int(v)
                got = walk(v, depth + 1)
                if got is not None:
                    return got
        elif isinstance(node, list):
            for item in node:
                got = walk(item, depth + 1)
                if got is not None:
                    return got
        return None

    return walk(user)


def extract_payment_verified(user_or_status: dict[str, Any] | None) -> bool | None:
    """Return the client's ``payment_verified`` flag, or ``None`` if unknown.

    Accepts either the full user dict (with a nested ``status`` object) or the
    ``status`` object itself, so it works on both the raw API payload and the
    ``client_status`` dict produced by :func:`get_client_status`.
    """
    if not isinstance(user_or_status, dict):
        return None
    status = user_or_status.get("status")
    if not isinstance(status, dict):
        # Caller may have passed the status object directly.
        status = user_or_status
    v = status.get("payment_verified")
    return bool(v) if isinstance(v, bool) else None


def get_client_status(session, owner_id: int | None) -> dict[str, Any]:
    """Look up the project owner's profile (country + completed-jobs history)."""
    if owner_id is None:
        return {"available": False, "reason": "missing_owner_id"}
    try:
        details = {
            "status": True,
            "reputation": True,
            "employer_reputation": True,
            "display_info": True,
            "country": True,
        }
        raw = fln_users.get_user_by_id(session, int(owner_id), user_details=details)
        user = raw.get("user") if isinstance(raw, dict) and isinstance(raw.get("user"), dict) else raw
        return {
            "available": True,
            "owner_id": int(owner_id),
            "username": (user or {}).get("username"),
            "status": (user or {}).get("status"),
            "reputation": (user or {}).get("reputation"),
            "employer_reputation": (user or {}).get("employer_reputation"),
            "country": (user or {}).get("country"),
            "country_code": extract_country_code((user or {}).get("country")),
            "country_name": extract_country_name((user or {}).get("country")),
            "completed_jobs": extract_completed_jobs_count(user),
            "payment_verified": extract_payment_verified(user),
        }
    except Exception as exc:
        return {"available": False, "reason": f"client_lookup_failed:{exc}"}


# ---------------------------------------------------------------------------
# Individual filters — each returns (passed, reason_if_failed)
# ---------------------------------------------------------------------------
def passes_currency(
    currency: str | None,
    skip_currencies: list[str],
) -> tuple[bool, str | None]:
    code = (currency or "").strip().upper()
    if not code:
        return True, None
    skip = {(c or "").strip().upper() for c in skip_currencies if (c or "").strip()}
    if code in skip:
        return False, f"currency_skipped:{code}"
    return True, None


def passes_recency(
    created_at: Any,
    max_age_seconds: int,
    now_epoch: float | None = None,
) -> tuple[bool, str | None]:
    """Reject projects created more than ``max_age_seconds`` ago.

    ``max_age_seconds <= 0`` disables the check. When the timestamp can't be
    parsed we allow the project through (don't drop on missing data).
    """
    if not max_age_seconds or max_age_seconds <= 0:
        return True, None
    ts = _to_epoch_seconds(created_at)
    if ts is None:
        return True, None
    now = now_epoch if now_epoch is not None else time.time()
    age = now - ts
    if age > max_age_seconds:
        return False, f"project_too_old:{int(age)}s"
    return True, None


def _bid_seconds_remaining(project: dict[str, Any], now_epoch: float | None = None) -> float | None:
    """Seconds until bidding closes = time_submitted + bidperiod(days) - now.

    Returns ``None`` if the submit time can't be parsed. Defaults bidperiod to 7
    days when missing (Freelancer's default).
    """
    ts = _to_epoch_seconds(project.get("created_at"))
    if ts is None:
        return None
    bidperiod = project.get("bidperiod")
    try:
        days = float(bidperiod) if bidperiod is not None else 7.0
    except (TypeError, ValueError):
        days = 7.0
    now = now_epoch if now_epoch is not None else time.time()
    return (ts + days * 86400.0) - now


def passes_bid_remaining(
    project: dict[str, Any],
    min_remaining_seconds: int,
    now_epoch: float | None = None,
) -> tuple[bool, str | None]:
    """Reject projects whose bidding closes in less than ``min_remaining_seconds``.

    ``min_remaining_seconds <= 0`` disables the check. Unknown bid-end times are
    allowed through (don't drop on missing data).
    """
    if not min_remaining_seconds or min_remaining_seconds <= 0:
        return True, None
    remaining = _bid_seconds_remaining(project, now_epoch)
    if remaining is None:
        return True, None
    if remaining < min_remaining_seconds:
        return False, f"bid_ending_soon:{int(remaining)}s_left"
    return True, None


def passes_keyword_blocklist(
    title: str | None,
    description: str | None,
    exclude_title_keywords: list[str],
    exclude_desc_keywords: list[str],
) -> tuple[bool, str | None]:
    """Reject a project if a blocked keyword appears in its title or description.

    Matching is case-insensitive substring (so ``wordpress`` also matches
    ``WordPress`` and ``wordpress-plugin``). Empty lists disable the respective
    check. Title and description have independent blocklists.
    """
    title_l = (title or "").lower()
    for kw in exclude_title_keywords:
        k = (kw or "").strip().lower()
        if k and k in title_l:
            return False, f"title_keyword_blocked:{k}"
    desc_l = (description or "").lower()
    for kw in exclude_desc_keywords:
        k = (kw or "").strip().lower()
        if k and k in desc_l:
            return False, f"desc_keyword_blocked:{k}"
    return True, None


def passes_skill_blocklist(
    skills_csv: str | None,
    exclude_skills: list[str],
) -> tuple[bool, str | None]:
    """Reject a project tagged with any blocked skill.

    ``skills_csv`` is the project's comma-separated skill badges. Matching is
    case-insensitive on the whole badge name OR as a substring (so "wordpress"
    blocks the "WordPress" badge and "WordPress Plugin"). Empty list disables it.
    """
    if not exclude_skills:
        return True, None
    badges = [b.strip().lower() for b in (skills_csv or "").split(",") if b.strip()]
    if not badges:
        return True, None
    for ex in exclude_skills:
        e = (ex or "").strip().lower()
        if not e:
            continue
        if any(e == b or e in b for b in badges):
            return False, f"skill_blocked:{e}"
    return True, None


def passes_budget(
    budget_min: float | None,
    budget_max: float | None,
    currency: str | None,
    min_budget_usd: int,
) -> tuple[bool, str | None]:
    max_budget = budget_max if budget_max is not None else budget_min
    if max_budget is None:
        # No budget info — allow through (scorer treats this as low-confidence).
        return True, None
    if float(max_budget) < float(min_budget_usd):
        return False, f"budget_too_low:{max_budget}"
    return True, None


def country_allowed(
    client_status: dict[str, Any],
    allow_countries: list[str],
    skip_countries: list[str],
) -> tuple[bool, str | None]:
    raw_country = client_status.get("country")
    code = (client_status.get("country_code") or "").strip().upper()
    name = (client_status.get("country_name") or "").strip().lower()

    def _norm(vals: list[str]) -> set[str]:
        out: set[str] = set()
        for v in vals:
            s = (v or "").strip()
            if s:
                out.add(s.upper())
                out.add(s.lower())
        return out

    allow = _norm(allow_countries)
    skip = _norm(skip_countries)
    hay = {code, name}
    if isinstance(raw_country, str):
        hay.add(raw_country.strip().upper())
        hay.add(raw_country.strip().lower())

    if allow and not any(x in allow for x in hay if x):
        return False, "country_not_allowed"
    if skip and any(x in skip for x in hay if x):
        return False, "country_blocked"
    return True, None


def passes_client_history(
    client_status: dict[str, Any],
    min_client_completed_jobs: int,
) -> tuple[bool, str | None]:
    min_completed = max(0, int(min_client_completed_jobs))
    if min_completed <= 0:
        # Filter disabled. Important for polling, where the search payload has no
        # owner_id, so completed_jobs is unknown (None) and would otherwise block.
        return True, None
    completed_jobs = client_status.get("completed_jobs")
    if completed_jobs is None or int(completed_jobs) < min_completed:
        return False, "client_completed_jobs_too_low"
    return True, None


def passes_payment_verified(
    client_status: dict[str, Any],
    require_payment_verified: bool,
) -> tuple[bool, str | None]:
    if not require_payment_verified:
        return True, None
    pv = client_status.get("payment_verified")
    if pv is None:
        pv = extract_payment_verified(client_status)
    if pv is True:
        return True, None
    # Distinguish "explicitly unverified" from "could not be determined".
    return False, "client_payment_unverified" if pv is False else "client_payment_unknown"


def evaluate_project(session, project: dict[str, Any], settings) -> tuple[bool, str | None, dict[str, Any]]:
    """Apply the pre-save filters in order: currency -> budget -> country -> client history.

    Returns ``(passed, reason, client_status)``. ``client_status`` is ``{}`` when
    the project is rejected before any API lookup is needed.
    """
    ok, reason = passes_recency(project.get("created_at"), settings.max_project_age_seconds)
    if not ok:
        return False, reason, {}

    ok, reason = passes_bid_remaining(project, settings.min_bid_remaining_seconds)
    if not ok:
        return False, reason, {}

    ok, reason = passes_currency(project.get("currency"), settings.skip_currencies)
    if not ok:
        return False, reason, {}

    ok, reason = passes_keyword_blocklist(
        project.get("title"),
        project.get("description"),
        settings.exclude_title_keywords,
        settings.exclude_desc_keywords,
    )
    if not ok:
        return False, reason, {}

    ok, reason = passes_skill_blocklist(project.get("skills"), settings.exclude_skills)
    if not ok:
        return False, reason, {}

    ok, reason = passes_budget(
        project.get("budget_min"),
        project.get("budget_max"),
        project.get("currency"),
        settings.min_budget_usd,
    )
    if not ok:
        return False, reason, {}

    client_status = get_client_status(session, project.get("owner_id"))

    ok, reason = country_allowed(client_status, settings.allow_countries, settings.skip_countries)
    if not ok:
        return False, reason, client_status

    ok, reason = passes_client_history(client_status, settings.min_client_completed_jobs)
    if not ok:
        return False, reason, client_status

    # ok, reason = passes_payment_verified(client_status, settings.require_payment_verified)
    # if not ok:
    #     return False, reason, client_status

    return True, None, client_status
