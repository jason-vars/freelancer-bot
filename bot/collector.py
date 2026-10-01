from __future__ import annotations

import json
from typing import Any

from freelancersdk.resources.projects import projects as fln_projects

from . import feed
from .db import upsert_project, is_project_seen, mark_project_seen, get_state, set_state
from .filters import evaluate_project, get_client_status, passes_recency
from .proposal_ai import ai_filter_project
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
        # Project upgrade flags (NDA, pf_only, sealed, ...) for the upgrade filter.
        "upgrades": p.get("upgrades"),
        # Keep the full API payload so we can inspect every field the API returns
        # and discover new filtering signals from real data.
        "raw_json": json.dumps(p, ensure_ascii=True, sort_keys=True, default=str),
    }


# The publisher only shares projects posted within this window (and so only looks
# up their clients). A cold start can page through ~1000 old-but-active projects,
# none of them recent enough for any account to act on.
FEED_MAX_AGE_SECONDS = 24 * 3600
# bot_state key holding feed items the publisher couldn't send yet (retried next poll).
FEED_PENDING_KEY = "feed_pending"


def _process_candidate(session, conn, settings, row: dict[str, Any],
                       client_status: dict[str, Any] | None = None) -> bool:
    """Filter one project and store it. Returns True when it passed (status 'new').

    Every candidate is run through the min-budget, country, and client-history
    checks (see ``filters.evaluate_project``). Survivors are stored with status
    ``new``; rejects are still persisted with status ``filtered`` and a
    ``filter_reason`` so you can review what was filtered out (and inspect
    ``raw_json`` to tune the filters). A known ``client_status`` skips the owner
    lookup."""
    pid = row["id"]
    passed, reason, _client_status = evaluate_project(session, row, settings, client_status)
    if not passed:
        # Some rejects are excluded entirely and never written to the DB:
        #   - skipped currencies (e.g. INR)
        #   - projects older than the recency window (most projects are)
        # Other rejects are kept as 'filtered' so they can be reviewed.
        if reason and (reason.startswith("currency_skipped") or reason.startswith("project_too_old") or reason.startswith("bid_ending_soon")):
            print(f"[{pid}] excluded (not saved): {reason}")
            return False
        upsert_project(conn, {**row, "status": "filtered", "score": 0, "filter_reason": reason})
        print(f"[{pid}] filtered: {reason}")
        return False

    # Optional natural-language AI gate (BOT_AI_FILTER_*). Runs only on
    # projects that already passed the cheap deterministic filters, so the
    # OpenAI cost is bounded. Fails open inside ai_filter_project.
    if settings.ai_filter_enabled and settings.openai_api_key and settings.ai_filter_criteria.strip():
        should_bid, ai_reason = ai_filter_project(
            settings.openai_api_key,
            settings.openai_model,
            settings.ai_filter_criteria,
            title=row["title"],
            description=row["description"],
            skills=row["skills"],
            budget_min=row["budget_min"],
            budget_max=row["budget_max"],
            currency=row["currency"],
        )
        if not should_bid:
            upsert_project(conn, {**row, "status": "filtered", "score": 0, "filter_reason": ai_reason})
            print(f"[{pid}] filtered by AI: {ai_reason}")
            return False

    upsert_project(conn, {**row, "status": "new", "score": 0, "filter_reason": None})
    print(f"[{pid}] stored (passed filters)")
    return True


def _fetch_from_feed(session, conn, settings) -> int:
    """Subscriber path: take new projects from the shared Supabase feed instead of
    calling Freelancer. Each project carries its client status, so filtering makes
    no API call either. Returns the number of projects that passed."""
    cursor = int(get_state(conn, feed.CURSOR_KEY) or 0)
    rows = feed.read_new(settings, cursor)
    new_count = 0
    for r in rows:
        row = r.get("project") or {}
        pid = int(row.get("id") or 0)
        if pid > 0 and not is_project_seen(conn, pid):
            mark_project_seen(conn, pid)
            # The feed holds every account's jobs; keep the ones matching ours.
            if feed.matches_keywords(row, settings.keywords):
                cs = r.get("client_status") or {"available": False, "reason": "not_in_feed"}
                if _process_candidate(session, conn, settings, row, cs):
                    new_count += 1
        # Advance per row so a crash mid-batch never replays what was handled.
        set_state(conn, feed.CURSOR_KEY, str(int(r["seq"])))
    print(f"[feed] read {len(rows)} project(s) from the shared feed")
    return new_count


def _flush_feed(conn, settings, items: list) -> None:
    """Publish ``items`` plus anything an earlier poll failed to send. On failure
    everything is kept in bot_state and retried next poll, so a Supabase outage
    never loses jobs for the subscribers."""
    pending = json.loads(get_state(conn, FEED_PENDING_KEY) or "[]") + [list(i) for i in items]
    if not pending:
        return
    try:
        sent = feed.publish(settings, [tuple(i) for i in pending])
        set_state(conn, FEED_PENDING_KEY, "[]")
        print(f"[feed] published {sent} project(s)")
    except Exception as exc:  # noqa: BLE001 - this account's own polling must go on
        # Cap the backlog so a long outage can't grow bot_state without bound.
        set_state(conn, FEED_PENDING_KEY, json.dumps(pending[-2000:], default=str))
        print(f"[feed] publish failed, will retry next poll: {exc}")


def fetch_and_store_projects(session, conn, settings, limit: int = 50) -> int:
    """Pull projects matching the configured keywords, filter them, and store only
    the survivors (see ``_process_candidate``).

    Shared feed (``BOT_FEED_MODE``): a subscriber reads from Supabase instead of
    calling Freelancer. The publisher searches with ``BOT_FEED_KEYWORDS`` (every
    account's keywords), shares each new project with its client status, and keeps
    for itself only those matching its own ``BOT_KEYWORDS``.

    Returns the number of projects that *passed* the filters.
    """
    mode = (settings.feed_mode or "off").lower()
    if mode == "subscribe":
        return _fetch_from_feed(session, conn, settings)
    publishing = mode == "publish"
    if publishing:
        feed.prune(settings)

    keywords = (settings.feed_keywords or settings.keywords) if publishing else settings.keywords
    # The feed query is broader than this account's own, so match its own locally.
    own_filter = publishing and bool(settings.feed_keywords)
    q = " ".join(keywords) if keywords else ""
    owners: dict[Any, dict[str, Any]] = {}  # client lookups, once per owner per poll

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
        to_publish = []
        for p in results:
            pid = int(p.get("id", 0))
            if pid <= 0:
                continue
            if is_project_seen(conn, pid):
                continue
            unseen_on_page += 1
            mark_project_seen(conn, pid)

            row = _project_to_row(p)
            cs = None
            if publishing and passes_recency(row.get("created_at"), FEED_MAX_AGE_SECONDS)[0]:
                owner = row.get("owner_id")
                if owner not in owners:
                    owners[owner] = get_client_status(session, owner)
                cs = owners[owner]
                to_publish.append((row, cs))
            if own_filter and not feed.matches_keywords(row, settings.keywords):
                continue  # shared for another account, not one of ours
            if _process_candidate(session, conn, settings, row, cs):
                new_count += 1

        # Per page, so subscribers get the newest jobs without waiting for the
        # whole poll to finish.
        if publishing:
            _flush_feed(conn, settings, to_publish)

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
