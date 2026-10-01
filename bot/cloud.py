"""Cloud mode: the poller works for the multi-user web app (``web/``) on Supabase.

Turned on by ``CLOUD_SUPABASE_URL`` + ``CLOUD_SUPABASE_KEY`` (the project's SECRET
key, which bypasses RLS) in the worker's .env. Then each polling cycle:

* settings come from the admin's ``app_settings`` table, laid over the .env
  (:func:`overlay_settings`), so the admin page controls keywords, filters, the poll
  interval, active hours, and the OpenAI / Telegram / Freelancer secrets;
* every project the poller stores or re-statuses is copied to the ``jobs`` table
  (:func:`sync_jobs`), where each user sees it with their own opened/applied state;
* a heartbeat lands in ``worker_status`` so the admin page shows the worker is alive,
  and the admin's "Fetch now" button is picked up from there (:func:`take_run_request`).

The schema is ``supabase/schema.sql``. Plain HTTP against PostgREST, like bot/feed.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests

from .db import get_state, set_state

# bot_state key: the last projects.updated_seq already pushed to the jobs table.
SYNC_CURSOR_KEY = "cloud_job_seq"
SYNC_BATCH = 200

# The last settings overlay that loaded, reapplied when Supabase is unreachable so a
# blip never drops the poller back to the .env's (possibly stale) values.
_last_overlay: dict[str, str] = {}
# Whether the ON/OFF line was printed for the current state (None = not yet), so the
# log says once, and again on every change, if the worker is feeding the website.
_announced: bool | None = None


def enabled() -> bool:
    return bool(_url() and _key())


def _url() -> str:
    return (os.getenv("CLOUD_SUPABASE_URL") or "").strip().rstrip("/")


def _key() -> str:
    return (os.getenv("CLOUD_SUPABASE_KEY") or "").strip()


def _headers(**extra: str) -> dict[str, str]:
    key = _key()
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", **extra}


def _rest(table: str) -> str:
    return f"{_url()}/rest/v1/{table}"


def _check(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 300:
        raise RuntimeError(f"Supabase {what} failed: HTTP {resp.status_code} {resp.text[:200]}")


def overlay_settings() -> None:
    """Copy the admin's app_settings into os.environ (they win over the .env).

    Called from config.load_settings right after the .env is re-read. The CLOUD_*
    connection keys themselves are never overridden."""
    global _last_overlay, _announced
    if not enabled():
        if _announced is not False:
            print("[cloud] OFF: CLOUD_SUPABASE_URL / CLOUD_SUPABASE_KEY not set in .env, "
                  "so jobs stay local and the website gets nothing.")
            _announced = False
        return
    try:
        resp = requests.get(_rest("app_settings"), headers=_headers(),
                            params={"select": "key,value"}, timeout=15)
        _check(resp, "settings read")
        rows = resp.json()
        _last_overlay = {
            str(r["key"]): str(r.get("value") or "")
            for r in rows if isinstance(r, dict) and r.get("key")
            and not str(r["key"]).startswith("CLOUD_")
        }
        if _announced is not True:
            print(f"[cloud] ON: {_url()} ({len(_last_overlay)} admin setting(s) loaded)")
            _announced = True
    except Exception as exc:  # noqa: BLE001 - fall back to the last good overlay
        print(f"[cloud] settings read failed, using the last loaded values: {exc}")
    os.environ.update(_last_overlay)


def _parse_raw(raw: str | None) -> dict[str, Any]:
    try:
        d = json.loads(raw or "")
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _posted_at(value: Any) -> str | None:
    """projects.created_at ('YYYY-MM-DD HH:MM:SS', UTC) -> ISO timestamptz."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _job_row(r) -> dict[str, Any]:
    raw = _parse_raw(r["raw_json"])
    upgrades = raw.get("upgrades")
    bidperiod = raw.get("bidperiod")
    return {
        "id": int(r["id"]),
        "title": r["title"] or "",
        "url": r["url"],
        "description": r["description"],
        "currency": r["currency"],
        "budget_min": r["budget_min"],
        "budget_max": r["budget_max"],
        "bid_count": r["bid_count"],
        "bid_avg": r["bid_avg"],
        "skills": r["skills"],
        "posted_at": _posted_at(r["created_at"]),
        "bidperiod": int(bidperiod) if isinstance(bidperiod, (int, float)) else None,
        "upgrades": upgrades if isinstance(upgrades, dict) else None,
        "score": int(r["score"] or 0),
        "status": r["status"] or "new",
        "filter_reason": r["filter_reason"],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def sync_jobs(conn) -> int:
    """Push every project changed since the last sync to the jobs table. The cursor
    only moves after Supabase accepted a batch, so an outage just delays the sync."""
    if not enabled():
        return 0
    cursor = int(get_state(conn, SYNC_CURSOR_KEY) or 0)
    sent = 0
    try:
        while True:
            rows = conn.execute(
                "SELECT * FROM projects WHERE updated_seq > ? ORDER BY updated_seq LIMIT ?",
                (cursor, SYNC_BATCH),
            ).fetchall()
            if not rows:
                break
            resp = requests.post(
                _rest("jobs"),
                headers=_headers(Prefer="resolution=merge-duplicates,return=minimal"),
                params={"on_conflict": "id"},
                data=json.dumps([_job_row(r) for r in rows], default=str),
                timeout=30,
            )
            _check(resp, "jobs sync")
            cursor = int(rows[-1]["updated_seq"])
            set_state(conn, SYNC_CURSOR_KEY, str(cursor))
            sent += len(rows)
    except Exception as exc:  # noqa: BLE001 - the poll itself must go on
        print(f"[cloud] jobs sync failed, will retry next cycle: {exc}")
    if sent:
        print(f"[cloud] synced {sent} job(s) to Supabase")
    return sent


def heartbeat(message: str) -> None:
    if not enabled():
        return
    try:
        resp = requests.patch(
            _rest("worker_status"), headers=_headers(Prefer="return=minimal"),
            params={"id": "eq.1"},
            data=json.dumps({"last_cycle_at": datetime.now(timezone.utc).isoformat(),
                             "message": message[:500]}),
            timeout=15,
        )
        _check(resp, "heartbeat")
    except Exception as exc:  # noqa: BLE001
        print(f"[cloud] heartbeat failed: {exc}")


def take_run_request() -> bool:
    """True (once) when the admin pressed "Fetch now" since the last check."""
    if not enabled():
        return False
    try:
        resp = requests.patch(
            _rest("worker_status"), headers=_headers(Prefer="return=representation"),
            params={"id": "eq.1", "run_requested_at": "not.is.null"},
            data=json.dumps({"run_requested_at": None}),
            timeout=15,
        )
        _check(resp, "run request")
        return bool(resp.json())
    except Exception as exc:  # noqa: BLE001
        print(f"[cloud] run-request check failed: {exc}")
        return False
