from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from freelancersdk.resources.users import users as fln_users

from .config import load_settings
from .db import (
    bid_exists_for_project,
    claim_project_for_notify,
    connect,
    count_bids_today,
    count_projects_by_filter_reason,
    delete_projects_by_currency,
    get_webhook_event,
    init_db,
    insert_bid,
    list_filtered_projects,
    list_projects_by_status,
    list_webhook_events,
    set_project_filtered,
    set_project_score_and_status,
    clear_seen_projects,
)
from .freelancer_client import make_session
from .collector import fetch_and_store_projects
from .filters import passes_recency, _to_epoch_seconds
from .util import within_active_hours, current_minutes, fmt_minutes
from .scorer import score_project
from .proposal_ai import (
    ai_price_and_duration,
    build_proposal_input,
    generate_proposal_openai,
)
from .bidder import BidRequest, place_bid
from .telegram_notify import (
    build_project_notification,
    send_telegram_message,
)
from .telegram_listener import run_telegram_listener
from .webhook import process_webhook_file, serve_webhook, _build_proposal, choose_bid_from_rules


def _print_json_block(label: str, raw: str | None) -> None:
    print(f"\n[{label}]")
    if not raw:
        print("(empty)")
        return
    try:
        parsed = json.loads(raw)
    except Exception:
        print(raw)
        return
    print(json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True))


def inspect_webhooks(event_id: int | None = None, limit: int = 3) -> None:
    conn = connect()
    init_db(conn)
    if event_id is not None:
        row = get_webhook_event(conn, event_id)
        if not row:
            print(f"Webhook event not found: id={event_id}")
            return
        print(f"Webhook event #{row['id']} | project_id={row['project_id']} | type={row['event_type']} | created_at={row['created_at']}")
        _print_json_block("payload_json", row["payload_json"])
        _print_json_block("extracted_project_json", row["extracted_project_json"])
        _print_json_block("project_detail_json", row["project_detail_json"])
        _print_json_block("result_json", row["result_json"])
        return

    rows = list_webhook_events(conn, limit=limit)
    if not rows:
        print("No webhook events recorded yet.")
        return
    for row in rows:
        print(f"\n=== Webhook event #{row['id']} | project_id={row['project_id']} | type={row['event_type']} | created_at={row['created_at']} ===")
        _print_json_block("extracted_project_json", row["extracted_project_json"])
        _print_json_block("project_detail_json", row["project_detail_json"])
        _print_json_block("result_json", row["result_json"])
        print("Use `webhook-inspect --id %s` to view full payload." % row["id"])

def inspect_filtered(limit: int = 20) -> None:
    """Show projects that were filtered out, with reasons, for tuning filters."""
    conn = connect()
    init_db(conn)

    summary = count_projects_by_filter_reason(conn)
    if not summary:
        print("No filtered projects recorded yet.")
        return

    print("=== Filtered projects by reason ===")
    for row in summary:
        print(f"  {row['filter_reason'] or '(none)'}: {row['c']}")

    print(f"\n=== Most recent {limit} filtered projects ===")
    for p in list_filtered_projects(conn, limit=limit):
        budget = f"{p['budget_min']}-{p['budget_max']} {p['currency'] or ''}".strip()
        print(
            f"\n#{p['id']} | reason={p['filter_reason']}"
            f"\n  title: {p['title']}"
            f"\n  budget: {budget} | skills: {p['skills'] or '-'}"
        )
    print(
        "\nTip: each row's raw_json column holds the full API payload. Inspect it with:"
        "\n  sqlite3 bot.sqlite3 \"SELECT raw_json FROM projects WHERE id=<ID>;\""
    )


def probe_api(project_id: int | None = None, owner_id: int | None = None, query: str | None = None) -> None:
    """Dump the full raw Freelancer API response for a project and/or user.

    Use this to discover every available field (e.g. the client's
    ``status.payment_verified`` flag) before writing/tuning filters. Pass one of:
      --project-id  : full project payload (all details) + its owner's full user payload
      --owner-id    : full user payload only
      --query       : search the first matching project, then dump it like --project-id
    """
    from freelancersdk.resources.projects import projects as fln_projects

    s = load_settings()
    session = make_session(s.fln_oauth_token, s.fln_url)

    # Request *every* detail block so nothing is hidden by a lean projection.
    project_details = {k: True for k in (
        "full_description", "jobs", "upgrades", "attachments", "files",
        "qualifications", "location", "nda_signature",
    )}
    user_details = {k: True for k in (
        "basic", "country", "profile_description", "display_info", "jobs",
        "membership", "location", "badge", "status", "reputation",
        "employer_reputation", "reputation_extra", "employer_reputation_extra",
        "responsiveness",
    )}

    if query and project_id is None:
        resp = fln_projects.search_projects(session, query=query, limit=1, active_only=True)
        projs = resp.get("projects") if isinstance(resp, dict) else resp
        if not projs:
            print(f"No projects matched query: {query!r}")
            return
        project_id = int(projs[0]["id"])
        print(f"Resolved query {query!r} -> project_id={project_id}")

    if project_id is not None:
        full = fln_projects.get_project_by_id(
            session, int(project_id),
            project_details=project_details, user_details=user_details,
        )
        proj = full.get("result") if isinstance(full, dict) and "result" in full else full
        _print_json_block(f"PROJECT {project_id} (full response)", json.dumps(full, default=str))
        if owner_id is None and isinstance(proj, dict):
            owner_id = proj.get("owner_id") or proj.get("owner_id_new")

    if owner_id is not None:
        raw = fln_users.get_user_by_id(session, int(owner_id), user_details=user_details)
        _print_json_block(f"USER {owner_id} (full response)", json.dumps(raw, default=str))
    elif project_id is not None:
        print(
            "\nNote: owner_id was not present in this project projection. "
            "Re-run with --owner-id <id> (the live webhook payload includes owner_id)."
        )


def _gen_one_proposal(s, conn, project: dict, save: bool) -> None:
    """Generate (and optionally save) a single cover letter from a project dict."""
    title = project.get("title") or "Test job"
    description = project.get("description") or "Build a small web app with auth and a dashboard."
    skills = project.get("skills") or ""
    budget_min, budget_max = project.get("budget_min"), project.get("budget_max")
    currency = project.get("currency") or "USD"
    project_id = project.get("id")

    print("\n=== INPUT ===")
    print(f"project_id: {project_id} | title: {title}")
    print(f"skills (badges): {skills or '(none)'}")
    print(f"budget: {budget_min}-{budget_max} {currency} | desc_len: {len(description)}")

    if not s.openai_api_key:
        print("No OPENAI_API_KEY set — cannot generate AI cover letter.")
        return

    print(f"Generating with model={s.openai_model} ...")
    try:
        proposal = generate_proposal_openai(
            api_key=s.openai_api_key,
            model=s.openai_model,
            data=build_proposal_input(
                s,
                title=title, description=description,
                budget_min=budget_min, budget_max=budget_max, currency=currency,
                skills=skills,
                questions=["What is your ideal deadline?"],
            ),
        )
    except Exception as exc:
        print(f"OpenAI generation FAILED: {type(exc).__name__}: {str(exc)[:300]}")
        return

    print("\n=== COVER LETTER ===")
    print(proposal)
    print(f"\n=== CHECKS === chars: {len(proposal)} (<1200: {len(proposal) < 1200}) | has ';': {';' in proposal} | empty-line: {chr(10)+chr(10) in proposal}")

    if save and project_id is not None:
        priced = None
        if s.ai_pricing_enabled and s.ai_pricing_rules.strip():
            priced = ai_price_and_duration(
                s.openai_api_key, s.openai_model, s.ai_pricing_rules,
                title=title, description=description, skills=skills,
                budget_min=budget_min, budget_max=budget_max, currency=currency,
            )
        if priced is not None:
            amount, period = priced
        else:
            amount = choose_bid_amount(budget_min, budget_max)
            period = choose_period_days(budget_min, budget_max)
        insert_bid(conn, int(project_id), None, float(amount), period, s.default_milestone_percent, proposal, status="proposal_test")
        print(f"Saved to DB: bids row (project_id={project_id}, status='proposal_test', amount={amount}, period_days={period})")


def gen_proposal(
    project_id: int | None = None,
    query: str | None = None,
    title: str | None = None,
    description: str | None = None,
    skills: str | None = None,
    save: bool = False,
    limit: int = 1,
) -> None:
    """Generate a cover letter for testing — from live project id/keyword(s) or ad-hoc text.

    Examples:
      gen-proposal --project-id 40481538 --save
      gen-proposal --query "firebase sendgrid email" --limit 5 --save
      gen-proposal --title "Fix Stripe webhook" --description "..." --skills "Node.js,Stripe"
    """
    from freelancersdk.resources.projects import projects as fln_projects

    s = load_settings()
    conn = connect(); init_db(conn)

    def _proj_from_api(p: dict) -> dict:
        jobs = p.get("jobs") or []
        b = p.get("budget") or {}
        cur = p.get("currency") or {}
        return {
            "id": p.get("id"),
            "title": p.get("title"),
            "description": p.get("description") or p.get("preview_description") or "",
            "skills": ",".join(str(j.get("name")) for j in jobs if isinstance(j, dict) and j.get("name")),
            "budget_min": b.get("minimum"), "budget_max": b.get("maximum"),
            "currency": (cur.get("code") if isinstance(cur, dict) else cur) or "USD",
        }

    # Mode 1: a list of real projects by keyword.
    if query and not project_id:
        session = make_session(s.fln_oauth_token, s.fln_url)
        resp = fln_projects.search_projects(
            session, query=query, limit=max(1, int(limit)), active_only=True,
            project_details={"full_description": True, "job_details": True},
        )
        projs = (resp.get("projects") if isinstance(resp, dict) else resp) or []
        if not projs:
            print(f"No projects matched query: {query!r}")
            return
        print(f"Generating proposals for {len(projs)} project(s) matching {query!r}...")
        for p in projs:
            _gen_one_proposal(s, conn, _proj_from_api(p), save)
        return

    # Mode 2: a single real project by id.
    if project_id:
        session = make_session(s.fln_oauth_token, s.fln_url)
        full = fln_projects.get_project_by_id(
            session, int(project_id),
            project_details={"full_description": True, "job_details": True},
        )
        proj = full.get("result") if isinstance(full, dict) and "result" in full else full
        row = _proj_from_api(proj)
        # allow manual overrides
        row["title"] = title or row["title"]
        row["description"] = description or row["description"]
        row["skills"] = skills or row["skills"]
        _gen_one_proposal(s, conn, row, save)
        return

    # Mode 3: fully ad-hoc text.
    _gen_one_proposal(s, conn, {
        "id": None, "title": title, "description": description, "skills": skills,
        "budget_min": None, "budget_max": None, "currency": "USD",
    }, save=False)


def test_telegram(text: str | None = None, project_id: int | None = None, query: str | None = None) -> None:
    """Send a test message to the configured Telegram chat to verify delivery.

    With --project-id/--query it fetches a real project and sends the actual
    notification layout (expandable description + skills + full-copy block).
    """
    s = load_settings()
    if not s.telegram_targets:
        print(
            "Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.\n"
            f"  TELEGRAM_BOT_TOKEN set: {bool(s.telegram_bot_token)}\n"
            f"  TELEGRAM_CHAT_ID set:   {bool(s.telegram_chat_ids)}\n"
            f"  delivery targets:       {len(s.telegram_targets)}"
        )
        return

    if project_id or query:
        from freelancersdk.resources.projects import projects as fln_projects
        session = make_session(s.fln_oauth_token, s.fln_url)
        if query and not project_id:
            resp = fln_projects.search_projects(
                session, query=query, limit=1, active_only=True,
                project_details={"full_description": True, "job_details": True},
            )
            projs = (resp.get("projects") if isinstance(resp, dict) else resp) or []
            if not projs:
                print(f"No projects matched query: {query!r}")
                return
            project_id = int(projs[0]["id"])
        full = fln_projects.get_project_by_id(
            session, int(project_id),
            project_details={"full_description": True, "job_details": True},
        )
        proj = full.get("result") if isinstance(full, dict) and "result" in full else full
        jobs = proj.get("jobs") or []
        b = proj.get("budget") or {}
        cur = proj.get("currency") or {}
        project = {
            "id": proj.get("id"), "title": proj.get("title"),
            "url": proj.get("seo_url") or proj.get("url"),
            "description": proj.get("description") or proj.get("preview_description") or "",
            "skills": ",".join(str(j.get("name")) for j in jobs if isinstance(j, dict) and j.get("name")),
            "budget_min": b.get("minimum"), "budget_max": b.get("maximum"),
            "currency": (cur.get("code") if isinstance(cur, dict) else cur) or "",
            "bid_count": (proj.get("bid_stats") or {}).get("bid_count"),
            "bid_avg": (proj.get("bid_stats") or {}).get("bid_avg"),
        }
        # Respect the skip-currency list so testing matches production behaviour.
        from .filters import passes_currency
        cur_ok, cur_reason = passes_currency(project["currency"], s.skip_currencies)
        if not cur_ok:
            print(f"Skipped (not sent): project {project['id']} -> {cur_reason}")
            return
        # Score it so the notification shows the Score line (like the real flow).
        sr = score_project(
            title=project["title"], description=project["description"] or "",
            skills_csv=project["skills"] or "", currency=project["currency"],
            budget_min=project["budget_min"], budget_max=project["budget_max"],
            min_budget_usd=s.min_budget_usd, required_skills=s.skills,
            min_skill_matches=s.min_skill_matches,
        )
        message = build_project_notification(project=project, result={"score": int(sr.score)})
    else:
        message = text or "<b>freelancer-bot</b>\nTest notification — Telegram is working."

    sent, failed = 0, 0
    for token, chat_id in s.telegram_targets:
        try:
            resp = send_telegram_message(token, chat_id, message)
            print(f"Sent. ok={resp.get('ok')} | chat_id={chat_id} | chars={len(message)}")
            sent += 1
        except Exception as exc:
            print(f"Failed to send to {chat_id}: {exc}")
            failed += 1
    print(f"Done. {sent} sent, {failed} failed across {len(s.telegram_targets)} target(s).")


def reset_seen() -> None:
    """Forget every previously-evaluated project so the next run re-scans the feed.

    Use this after loosening filters: polling dedups against the seen-set, so a
    project already evaluated under stricter filters is never re-checked otherwise.
    """
    conn = connect()
    init_db(conn)
    cleared = clear_seen_projects(conn)
    print(f"Cleared {cleared} seen project id(s). The next run-loop cycle will re-scan the feed.")


def purge_currencies(currencies: list[str] | None = None) -> None:
    """Delete already-stored projects in skipped currencies (default: configured skip list)."""
    s = load_settings()
    targets = currencies if currencies else s.skip_currencies
    conn = connect()
    init_db(conn)
    removed = delete_projects_by_currency(conn, targets)
    print(f"Removed {removed} project(s) in currencies: {', '.join(targets) or '(none)'}")


def get_my_user_id(session) -> int:
    # `freelancersdk==0.1.20` exposes `get_self_user_id` / `get_self`.
    try:
        return int(fln_users.get_self_user_id(session))
    except Exception:
        me = fln_users.get_self(session)
        if isinstance(me, dict) and "result" in me:
            return int(me["result"]["id"])
        return int(me["id"])

def choose_bid_amount(budget_min: float | None, budget_max: float | None) -> float:
    # Simple heuristic: bid near mid or max, but not above.
    if budget_min is None and budget_max is None:
        return 200.0
    if budget_max is None:
        return float(budget_min)
    if budget_min is None:
        return float(budget_max)
    return float((budget_min + budget_max) / 2)


def choose_period_days(budget_min: float | None, budget_max: float | None) -> int:
    # Delivery period: 1 day for small projects (max budget < 300), else 7 days.
    max_budget = budget_max if budget_max is not None else budget_min
    if max_budget is not None and float(max_budget) < 300:
        return 1
    return 7


def _notify_telegram_polling(conn, settings, project: dict[str, Any], client_status: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    # Notifications muted (master switch off): treat like "not configured" — the
    # project is still collected and marked handled, just no alert is sent.
    if not settings.notify_enabled or not settings.telegram_targets:
        return None
    # Outside the notification window: DROP the alert (don't defer). The project is
    # still collected/browsable, but it is not alerted now and never later — the user
    # wants alerts only for jobs found from the window start onward, not a backlog
    # replayed when the window opens. Returned as a distinct outcome the caller marks
    # handled so it isn't retried.
    if not settings.notify_window_open():
        return {"sent": False, "out_of_window": True}
    msg = build_project_notification(project=project, client_status=client_status, result=result)
    sent_any = False
    last_err: str | None = None
    for token, chat_id in settings.telegram_targets:
        try:
            send_telegram_message(token, chat_id, msg)
            sent_any = True
        except Exception as exc:
            last_err = str(exc)
            print(f"[telegram] send to {chat_id} failed: {exc}")
    # Sent if it reached at least one chat. Only when ALL chats fail do we report a
    # failure so the row retries (avoids re-spamming chats that already got it).
    if sent_any:
        return {"sent": True, "ok": True}
    return {"sent": False, "error": last_err}

def _maybe_save_proposal(conn, settings, p) -> None:
    """When BOT_SAVE_PROPOSALS is on, generate a proposal for a matching project and
    save it to the bids table (status 'proposal_saved') for review — no bid placed.

    Idempotent: skips projects that already have any bid/draft row, so each project
    is only generated once even though polling re-scans every cycle. Failures are
    logged but never abort the polling cycle."""
    if not settings.save_proposals:
        return
    pid = int(p["id"])
    if bid_exists_for_project(conn, pid):
        return
    try:
        project = dict(p)
        proposal = _build_proposal(settings, project)
        ruled = choose_bid_from_rules(project.get("currency"), project.get("budget_min"), project.get("budget_max"), settings.bid_rules)
        if ruled is not None:
            amount, period = ruled
        else:
            amount = choose_bid_amount(project.get("budget_min"), project.get("budget_max"))
            period = choose_period_days(project.get("budget_min"), project.get("budget_max"))
        insert_bid(conn, pid, None, float(amount), int(period), settings.default_milestone_percent, proposal, status="proposal_saved")
        print(f"[{pid}] proposal saved to DB (status='proposal_saved', amount={amount}, period_days={period})")
    except Exception as exc:
        print(f"[{pid}] proposal save FAILED: {exc}")

def run(dry_run_override: bool | None = None, notify_floor_epoch: float | None = None) -> None:
    """One fetch -> score -> notify cycle.

    ``notify_floor_epoch`` is an absolute cutoff: projects posted BEFORE this epoch
    are never alerted (the continuous loop passes its own start time, so a launch at
    9:00 AM never notifies jobs posted earlier, even ones still inside the recency
    window). ``None`` disables the floor (one-shot ``run`` behaves as before)."""
    s = load_settings()
    if dry_run_override is not None:
        dry_run = dry_run_override
    else:
        dry_run = s.dry_run

    conn = connect()
    init_db(conn)

    session = make_session(s.fln_oauth_token, s.fln_url)

    # Discovery + pre-save filtering (min-budget / country / client-history) all
    # happen inside fetch_and_store_projects, so only survivors reach the DB.
    new_count = fetch_and_store_projects(session, conn, s, limit=50)
    print(f"Fetched/stored {new_count} new projects")

    # Score each new project and notify INSTANTLY for great matches. Notify-only:
    # no bids placed, no deferred bid loop. Also re-attempt any 'alert_failed'
    # rows (a previous send that hit a rate-limit / network error): they retry
    # every cycle until delivered or until recency expires them below.
    pending = list_projects_by_status(conn, "new", limit=50) + list_projects_by_status(conn, "alert_failed", limit=50)
    notified = 0
    failed = 0
    for p in pending:
        # Re-apply recency at notify time. A stale row (e.g. stored earlier when
        # the window was wider/disabled, or an alert_failed row that has since
        # aged out) must NOT be notified under the current
        # BOT_MAX_PROJECT_AGE_SECONDS — recency is otherwise only checked at fetch
        # time.
        rec_ok, rec_reason = passes_recency(p["created_at"], s.max_project_age_seconds)
        if not rec_ok:
            print(f"[{p['id']}] skip (stale): {rec_reason}")
            set_project_filtered(conn, int(p["id"]), rec_reason)
            continue
        # Absolute launch-time floor: never alert on jobs posted before the bot
        # started this session. Marked handled so it is not retried next cycle.
        if notify_floor_epoch is not None:
            ts = _to_epoch_seconds(p["created_at"])
            if ts is not None and ts < notify_floor_epoch:
                set_project_filtered(conn, int(p["id"]), "posted_before_session_start")
                print(f"[{p['id']}] skip (posted before bot started this session)")
                continue
        res = score_project(
            title=p["title"],
            description=p["description"] or "",
            skills_csv=p["skills"] or "",
            currency=p["currency"],
            budget_min=p["budget_min"],
            budget_max=p["budget_max"],
            min_budget_usd=s.min_budget_usd,
            required_skills=s.skills,
            min_skill_matches=s.min_skill_matches,
        )
        # Alert on every job that passes the (configurable) filters, gated only by
        # BOT_MIN_SCORE (set 0 to alert on all). Notify INSTANTLY, no DB bid.
        notify = int(res.score) >= int(s.min_score)
        if not notify:
            set_project_score_and_status(conn, int(p["id"]), int(res.score), "skipped")
            print(f"[{p['id']}] score={res.score} min_score={s.min_score} -> skip")
            continue

        # Outside the notification window: collect but DON'T alert, and mark it
        # handled ('notify_skipped') so it is never alerted later either. Checked
        # BEFORE the claim below so out-of-window jobs are not left stuck mid-claim.
        # This is what makes alerts start fresh at the window edge instead of
        # replaying a backlog of jobs found while the window was closed.
        if s.notify_enabled and s.telegram_targets and not s.notify_window_open():
            set_project_score_and_status(conn, int(p["id"]), int(res.score), "notify_skipped")
            print(f"[{p['id']}] score={res.score} -> alert skipped (outside notification window)")
            continue

        # ATOMIC CLAIM: transition the row out of 'new'/'alert_failed' in one UPDATE
        # so exactly ONE cycle/instance can send this job. Losers skip — this is the
        # guard against duplicate notifications (same job sent twice).
        if not claim_project_for_notify(conn, int(p["id"]), int(res.score)):
            print(f"[{p['id']}] already claimed for alert (another cycle/instance) -> skip")
            continue

        # Optionally generate + save a proposal draft to the DB for review (testing
        # before enabling real bids). Guarded by BOT_SAVE_PROPOSALS; idempotent.
        _maybe_save_proposal(conn, s, p)

        # Mark the final status by the SEND OUTCOME. send_telegram_message already
        # retries transient errors; if it still fails we record 'alert_failed' and
        # re-claim it next cycle.
        outcome = _notify_telegram_polling(
            conn, s, dict(p), None,
            {"ok": True, "project_id": int(p["id"]), "score": int(res.score)},
        )
        if outcome is None or outcome.get("sent"):
            # None => Telegram not configured (notifications disabled): treat as
            # handled so it isn't retried forever.
            set_project_score_and_status(conn, int(p["id"]), int(res.score), "alerted")
            print(f"[{p['id']}] score={res.score} min_score={s.min_score} -> ALERT sent")
            notified += 1
        else:
            set_project_score_and_status(conn, int(p["id"]), int(res.score), "alert_failed")
            print(f"[{p['id']}] score={res.score} -> ALERT FAILED ({outcome.get('error')}); will retry next cycle")
            failed += 1

    print(f"Notified {notified} project(s)." + (f" {failed} failed (will retry)." if failed else ""))

def run_loop(interval_seconds: int | None = None, dry_run_override: bool | None = None) -> None:
    s = load_settings()
    interval = int(interval_seconds if interval_seconds is not None else s.poll_interval_seconds)
    if interval <= 0:
        raise ValueError("interval must be > 0 seconds")

    # Absolute floor captured at launch: jobs posted before the bot started this
    # session are treated as backlog and never alerted (tighter than the rolling
    # recency window). A restart re-floors to the new start time.
    session_start = time.time()
    print(f"Auto polling started. interval={interval}s dry_run={'yes' if dry_run_override else 'env/default'} "
          f"(alerting only jobs posted from launch onward)")
    while True:
        started = int(time.time())
        # Reload settings each cycle so interval / active-hours edits made in the
        # web UI take effect without a restart.
        cur = load_settings()
        if interval_seconds is None:
            interval = max(1, int(cur.poll_interval_seconds))

        # Evaluate the active window in the configured timezone (or machine local
        # time when no offset is set). Logs the perceived clock so a timezone
        # mismatch is obvious.
        now_min = current_minutes(cur.active_tz_offset)
        tz_label = "machine local" if cur.active_tz_offset is None else f"UTC{cur.active_tz_offset:+g}"
        if not within_active_hours(cur.active_start, cur.active_end, now_minutes=now_min):
            print(f"\n[{started}] Outside active hours (window {cur.active_start}-{cur.active_end}, "
                  f"now {fmt_minutes(now_min)} {tz_label}); skipping cycle.")
            time.sleep(interval)
            continue

        print(f"\n[{started}] Running fetch/score cycle...")
        try:
            run(dry_run_override=dry_run_override, notify_floor_epoch=session_start)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Run failed: {exc}")

        elapsed = max(0, int(time.time()) - started)
        sleep_for = max(1, interval - elapsed)
        print(f"Cycle complete in {elapsed}s. Sleeping {sleep_for}s...")
        time.sleep(sleep_for)

def _read_pid(path: str = "bot.pid") -> int | None:
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """True if a process with this id is currently running. Used to detect a still-
    running bot. A stale pid (dead process) returns False so it can be overwritten."""
    import os
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already accepting TCP connections on ``host:port``.

    Used as the reliable single-instance lock for ``serve`` — if the web UI port
    answers, another bot is already running, so a second one must not start (two
    polling loops would double-send every notification)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        # connect_ex == 0 means the port accepted the connection (someone's listening).
        return sock.connect_ex((host, port)) == 0


def serve_app(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the web UI and the polling loop together in ONE process.

    Used by the double-click launcher so there's a single thing to start/stop. The
    polling loop runs in a daemon background thread (it dies with the process); the
    web UI owns the main thread. When launched headless (pythonw, no console),
    stdout/stderr are redirected to bot.log and the PID is written to bot.pid so the
    stop script can find this exact process.

    A single-instance guard refuses to start if another bot is already running —
    two polling loops would double-send every notification.
    """
    import os
    import threading
    from .webui import serve_webui

    # Headless launch (pythonw) has no console: capture output to a log file.
    if sys.stdout is None or sys.stderr is None:
        log = open("bot.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log

    pid_path = "bot.pid"
    # Single-instance guard #1 (primary, reliable): if the web UI port is already
    # accepting connections, another bot is live. This does NOT depend on writing
    # bot.pid (which can silently fail on macOS / in protected folders, leaving the
    # file guard useless) and is checked BEFORE the polling thread starts, so a
    # second launch can never sneak in a duplicate poll loop and double-notify.
    if _port_in_use(host, port):
        print(
            f"A bot is already running on {host}:{port}; not starting a second one "
            f"(that would send every notification twice). Open http://{host}:{port}, "
            f"or stop the existing one first."
        )
        return
    # Single-instance guard #2 (best-effort): a live PID from a previous run.
    existing = _read_pid(pid_path)
    if existing and existing != os.getpid() and _pid_alive(existing):
        print(
            f"A bot is already running (PID {existing}); not starting a second one. "
            f"Open http://{host}:{port}, or run stop-bot first if you want to restart."
        )
        return
    try:
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    def _loop() -> None:
        try:
            run_loop()
        except Exception as exc:  # never let a loop crash take down the web UI
            print(f"[poll-loop] stopped: {type(exc).__name__}: {exc}")

    threading.Thread(target=_loop, name="poll-loop", daemon=True).start()
    print(f"Bot started. Web UI: http://{host}:{port} | polling loop running in background.")
    try:
        serve_webui(host=host, port=port)
    finally:
        try:
            os.remove(pid_path)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freelancer-bid-bot")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Fetch projects -> score -> generate proposal -> bid")
    run_p.add_argument("--dry-run", action="store_true", help="Do not place bids; only save drafts")
    run_loop_p = sub.add_parser("run-loop", help="Continuously run fetch/score cycle on an interval")
    run_loop_p.add_argument("--dry-run", action="store_true", help="Do not place bids; only save drafts")
    run_loop_p.add_argument("--interval-seconds", type=int, default=None, help="Polling interval (default from BOT_POLL_INTERVAL_SECONDS or 300)")
    webhook_once = sub.add_parser("webhook-once", help="Process one webhook JSON payload from file")
    webhook_once.add_argument("--payload-file", required=True, help="Path to webhook JSON payload file")
    webhook_once.add_argument("--delay-seconds", type=int, default=None, help="Wait before proposal/bid")
    webhook_once.add_argument("--dry-run", action="store_true", help="Do not place bid; save draft only")

    webhook_listen = sub.add_parser("webhook-listen", help="Run webhook HTTP server")
    webhook_listen.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    webhook_listen.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    webhook_listen.add_argument("--delay-seconds", type=int, default=None, help="Wait before proposal/bid")
    webhook_listen.add_argument("--dry-run", action="store_true", help="Do not place bid; save draft only")
    webhook_inspect = sub.add_parser("webhook-inspect", help="Inspect saved webhook payloads/results for filter tuning")
    webhook_inspect.add_argument("--id", type=int, default=None, help="Show a specific webhook event id")
    webhook_inspect.add_argument("--limit", type=int, default=3, help="How many recent events to show (default: 3)")

    filtered_p = sub.add_parser("filtered", help="Show projects that were filtered out, with reasons")
    filtered_p.add_argument("--limit", type=int, default=20, help="How many recent filtered projects to show (default: 20)")

    sub.add_parser("reset-seen", help="Reset the polling high-water mark so the next run re-scans all projects")

    purge_p = sub.add_parser("purge-currency", help="Delete stored projects in skipped currencies (e.g. INR)")
    purge_p.add_argument("--currencies", default=None, help="Comma-separated codes to purge (default: BOT_SKIP_CURRENCIES)")

    probe_p = sub.add_parser("probe", help="Dump the full raw API response for a project/user (discover available fields)")
    probe_p.add_argument("--project-id", type=int, default=None, help="Project id to dump (full payload + owner)")
    probe_p.add_argument("--owner-id", type=int, default=None, help="User/client id to dump (full payload)")
    probe_p.add_argument("--query", default=None, help="Search keyword; dumps the first matching project")

    gen_p = sub.add_parser("gen-proposal", help="Generate a cover letter for testing (from a project id/keyword or ad-hoc text)")
    gen_p.add_argument("--project-id", type=int, default=None, help="Live project id to generate from")
    gen_p.add_argument("--query", default=None, help="Search keyword; uses the first matching project")
    gen_p.add_argument("--title", default=None, help="Ad-hoc job title")
    gen_p.add_argument("--description", default=None, help="Ad-hoc job description")
    gen_p.add_argument("--skills", default=None, help="Ad-hoc skill badges, comma-separated")
    gen_p.add_argument("--limit", type=int, default=1, help="With --query: how many projects to generate/save (default 1)")
    gen_p.add_argument("--save", action="store_true", help="Also save the result(s) to the bids table (status=proposal_test)")

    tg_p = sub.add_parser("test-telegram", help="Send a test message to your configured Telegram chat")
    tg_p.add_argument("--text", default=None, help="Custom message text (HTML allowed)")
    tg_p.add_argument("--project-id", type=int, default=None, help="Send a real project's full notification layout")
    tg_p.add_argument("--query", default=None, help="Search keyword; send the first matching project's notification")

    sub.add_parser("telegram-listen", help="Long-poll Telegram for 'Mark read' button presses (run alongside run-loop)")

    webui_p = sub.add_parser("webui", help="Launch the local settings web UI to edit .env (filters, search, bidding)")
    webui_p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1 = localhost only)")
    webui_p.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")

    serve_p = sub.add_parser("serve", help="Run the web UI AND the polling loop together in one process")
    serve_p.add_argument("--host", default="127.0.0.1", help="Web UI bind host (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=8765, help="Web UI bind port (default: 8765)")

    sub.add_parser("sync-applied", help="Mark every project you have a live bid on (from the Freelancer API) as applied")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        run(dry_run_override=True if args.dry_run else None)
        return 0
    if args.cmd == "run-loop":
        run_loop(
            interval_seconds=args.interval_seconds,
            dry_run_override=True if args.dry_run else None,
        )
        return 0
    if args.cmd == "webhook-once":
        result = process_webhook_file(
            path=args.payload_file,
            delay_seconds=args.delay_seconds,
            dry_run_override=True if args.dry_run else None,
        )
        print(result)
        return 0
    if args.cmd == "webhook-listen":
        serve_webhook(
            host=args.host,
            port=args.port,
            delay_seconds=args.delay_seconds,
            dry_run_override=True if args.dry_run else None,
        )
        return 0
    if args.cmd == "webhook-inspect":
        inspect_webhooks(event_id=args.id, limit=args.limit)
        return 0
    if args.cmd == "filtered":
        inspect_filtered(limit=args.limit)
        return 0
    if args.cmd == "reset-seen":
        reset_seen()
        return 0
    if args.cmd == "purge-currency":
        codes = None
        if args.currencies:
            codes = [c.strip() for c in args.currencies.split(",") if c.strip()]
        purge_currencies(codes)
        return 0
    if args.cmd == "probe":
        if args.project_id is None and args.owner_id is None and not args.query:
            print("Provide one of: --project-id, --owner-id, or --query")
            return 1
        probe_api(project_id=args.project_id, owner_id=args.owner_id, query=args.query)
        return 0
    if args.cmd == "gen-proposal":
        gen_proposal(
            project_id=args.project_id,
            query=args.query,
            title=args.title,
            description=args.description,
            skills=args.skills,
            save=args.save,
            limit=args.limit,
        )
        return 0
    if args.cmd == "test-telegram":
        test_telegram(text=args.text, project_id=args.project_id, query=args.query)
        return 0
    if args.cmd == "telegram-listen":
        run_telegram_listener()
        return 0
    if args.cmd == "webui":
        from .webui import serve_webui
        serve_webui(host=args.host, port=args.port)
        return 0
    if args.cmd == "serve":
        serve_app(host=args.host, port=args.port)
        return 0

    if args.cmd == "sync-applied":
        from .webui import _sync_applied_from_freelancer
        _code, res = _sync_applied_from_freelancer()
        if not res.get("ok"):
            print(f"Sync failed: {res.get('message')}")
            return 1
        n = int(res.get("marked_existing", 0)) + int(res.get("stored_new", 0))
        print(f"Synced {res.get('bids_projects', 0)} bid(s): {n} marked applied "
              f"({res.get('stored_new', 0)} newly stored, {res.get('already_marked', 0)} already).")
        return 0

    parser.print_help()
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
