from __future__ import annotations

import json
from typing import Any

from freelancersdk.resources.projects import projects as fln_projects

from .db import upsert_project, is_project_seen, mark_project_seen
from .filters import evaluate_project
from .util import safe_get


def _project_to_row(p: dict[str, Any]) -> dict[str, Any]:
    # API payloads vary by fields requested; keep it defensive.
    budget = p.get("budget") or {}
    top_currency = p.get("currency")
    if isinstance(top_currency, dict):
        currency = top_currency.get("code")
    else:
        currency = top_currency
    if not currency:
        currency = (budget.get("currency") or {}).get("code")
    bid_stats = p.get("bid_stats") or {}

    return {
        "id": int(p["id"]),
        "title": p.get("title") or "",
        "url": p.get("seo_url") or p.get("url"),
        "description": p.get("description") or p.get("preview_description") or "",
        "currency": currency,
        "budget_min": safe_get(budget, "minimum"),
        "budget_max": safe_get(budget, "maximum"),
        "bid_count": safe_get(bid_stats, "bid_count"),
        "bid_avg": safe_get(bid_stats, "bid_avg"),
        "skills": ",".join([str(s.get("name")) for s in (p.get("jobs") or []) if isinstance(s, dict)]),
        "created_at": p.get("time_submitted") or p.get("date_submitted") or p.get("time_created"),
        "bidperiod": p.get("bidperiod"),
        "owner_id": p.get("owner_id") or p.get("user_id"),
        # Keep the full API payload so we can inspect every field the API returns
        # and discover new filtering signals from real data.
        "raw_json": json.dumps(p, ensure_ascii=True, sort_keys=True, default=str),
    }


def fetch_and_store_projects(session, conn, settings, limit: int = 50) -> int:
    """Pull projects matching the configured keywords, filter them, and store only
    the survivors.

    Every candidate is run through the min-budget, country, and client-history
    checks (see ``filters.evaluate_project``) *before* it would normally be
    processed. Survivors are stored with status ``new``; rejects are still
    persisted with status ``filtered`` and a ``filter_reason`` so you can review
    what was filtered out (and inspect ``raw_json`` to tune the filters).

    Returns the number of projects that *passed* the filters.
    """
    keywords = settings.keywords
    q = " ".join(keywords) if keywords else ""

    # NOTE: the search API's DEFAULT sort is already newest-first (by submit
    # time), which is what the per-page early-stop below relies on ("a whole page
    # surfaced nothing new => caught up, stop"). Do NOT pass an explicit
    # sort_field: in this API reverse_sort=True means OLDEST-first, so forcing a
    # sort here surfaced years-old projects and made every poll fetch 0 new.
    new_count = 0
    offset = 0
    page_size = max(1, int(limit))
    # Safety bound on a cold start (empty seen-set) so one poll can't paginate
    # through the entire keyword feed. page_size * MAX_PAGES candidates per poll.
    MAX_PAGES = 20
    pages = 0

    while pages < MAX_PAGES:
        pages += 1
        # `freelancersdk==0.1.20` uses search_projects for keyword discovery.
        # Request full_description: without it the API only returns a 100-char
        # `preview_description` and an empty `description`, so we'd store a
        # truncated snippet instead of the whole project text.
        resp = fln_projects.search_projects(
            session,
            query=q,
            limit=page_size,
            offset=offset,
            active_only=True,
            project_details={"full_description": True, "job_details": True},
        )
        if isinstance(resp, dict):
            results = resp.get("projects") or []
        elif isinstance(resp, list):
            results = resp
        else:
            results = []

        if not results:
            break

        # Count projects on this page we have not evaluated before. Dedup is by
        # a persistent seen-set, not a max-id high-water mark, so a relevant
        # project is never skipped merely because its id is lower than one seen
        # in an earlier page/poll (Freelancer search is not strictly id-ordered).
        unseen_on_page = 0
        for p in results:
            pid = int(p.get("id", 0))
            if pid <= 0:
                continue
            if is_project_seen(conn, pid):
                continue
            unseen_on_page += 1
            mark_project_seen(conn, pid)

            row = _project_to_row(p)
            passed, reason, _client_status = evaluate_project(session, row, settings)
            if not passed:
                # Some rejects are excluded entirely and never written to the DB:
                #   - skipped currencies (e.g. INR)
                #   - projects older than the recency window (most projects are)
                # Other rejects are kept as 'filtered' so they can be reviewed.
                if reason and (reason.startswith("currency_skipped") or reason.startswith("project_too_old") or reason.startswith("bid_ending_soon")):
                    print(f"[{pid}] excluded (not saved): {reason}")
                    continue
                upsert_project(conn, {**row, "status": "filtered", "score": 0, "filter_reason": reason})
                print(f"[{pid}] filtered: {reason}")
                continue

            upsert_project(conn, {**row, "status": "new", "score": 0, "filter_reason": None})
            new_count += 1
            print(f"[{pid}] stored (passed filters)")

        # Once a whole page surfaces nothing new, we have caught up to the
        # already-evaluated backlog; stop paginating.
        if unseen_on_page == 0:
            break

        # Last page (fewer than requested) => no more to fetch.
        if len(results) < page_size:
            break

        offset += page_size

    if pages >= MAX_PAGES:
        print(f"[collector] reached MAX_PAGES={MAX_PAGES}; stopped early (more unseen projects may remain this poll)")

    return new_count
