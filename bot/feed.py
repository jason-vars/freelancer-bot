"""Shared job feed on Supabase: ONE Freelancer token fetches jobs, every account reads.

Without it, each account polls Freelancer's search API (and looks up every client)
with its own token, so N accounts make N times the calls for the same jobs. With it:

* the PUBLISHER account (``BOT_FEED_MODE=publish``) searches Freelancer as usual,
  looks up each new project's client once, and writes both to the ``feed_projects``
  table;
* every SUBSCRIBER account (``BOT_FEED_MODE=subscribe``) reads new rows from that
  table instead of calling Freelancer, then applies its OWN keywords and filters
  locally.

Bidding, proposal generation and the userscript still use each account's own token.

Talks to Supabase's REST API (PostgREST) with plain HTTP, so there is no extra
dependency. The table is created by ``deploy/supabase_feed.sql``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

TABLE = "feed_projects"
# Subscribers read at most this many rows per poll; the rest arrive next poll.
PAGE_SIZE = 500
# The publisher deletes rows older than this so the table stays small. Longer than
# any sensible BOT_MAX_PROJECT_AGE_SECONDS, so a subscriber that was down for a
# while still catches up on everything it could act on.
RETENTION = timedelta(days=3)
# bot_state key holding the last feed row (seq) a subscriber has processed.
CURSOR_KEY = "feed_cursor"


class FeedError(RuntimeError):
    pass


def _base(settings) -> tuple[str, dict[str, str]]:
    url = (settings.supabase_url or "").strip().rstrip("/")
    key = (settings.supabase_key or "").strip()
    if not url or not key:
        raise FeedError("Shared feed is on but SUPABASE_URL / SUPABASE_KEY are not set.")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    return f"{url}/rest/v1/{TABLE}", headers


def _check(resp: requests.Response, what: str) -> None:
    if resp.status_code >= 300:
        raise FeedError(f"Supabase {what} failed: HTTP {resp.status_code} {resp.text[:200]}")


def publish(settings, items: list[tuple[dict[str, Any], dict[str, Any]]]) -> int:
    """Write ``(project_row, client_status)`` pairs to the feed. A project that is
    already there is left untouched (so it is never delivered twice). Returns the
    number of rows sent."""
    if not items:
        return 0
    url, headers = _base(settings)
    payload = [
        {"id": int(row["id"]), "project": row, "client_status": cs or {}}
        for row, cs in items
    ]
    resp = requests.post(
        url,
        headers={**headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
        params={"on_conflict": "id"},
        data=json.dumps(payload, default=str),
        timeout=30,
    )
    _check(resp, "publish")
    return len(payload)


def prune(settings) -> None:
    """Delete feed rows past RETENTION. Best effort: a failure is only logged."""
    try:
        url, headers = _base(settings)
        cutoff = (datetime.now(timezone.utc) - RETENTION).isoformat()
        resp = requests.delete(url, headers=headers, params={"published_at": f"lt.{cutoff}"}, timeout=30)
        _check(resp, "prune")
    except Exception as exc:  # noqa: BLE001 - housekeeping must never stop a poll
        print(f"[feed] prune skipped: {exc}")


def read_new(settings, after_seq: int) -> list[dict[str, Any]]:
    """Feed rows published after ``after_seq``, oldest first. Each row has ``seq``,
    ``project`` (the collector's row dict) and ``client_status``."""
    url, headers = _base(settings)
    resp = requests.get(
        url,
        headers=headers,
        params={
            "select": "seq,project,client_status",
            "seq": f"gt.{int(after_seq)}",
            "order": "seq.asc",
            "limit": str(PAGE_SIZE),
        },
        timeout=30,
    )
    _check(resp, "read")
    rows = resp.json()
    return rows if isinstance(rows, list) else []


def matches_keywords(row: dict[str, Any], keywords: list[str]) -> bool:
    """Local stand-in for Freelancer's keyword search: the feed carries jobs for
    EVERY account's keywords, so each account keeps only those mentioning one of
    its own. No keywords = keep everything (same as an empty search query)."""
    kws = [k.strip().lower() for k in keywords or [] if k.strip()]
    if not kws:
        return True
    text = " ".join(
        str(row.get(k) or "") for k in ("title", "description", "skills")
    ).lower()
    return any(k in text for k in kws)
