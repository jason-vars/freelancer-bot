from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from freelancersdk.resources.projects import projects as fln_projects
from freelancersdk.resources.users import users as fln_users

from .bidder import BidRequest, place_bid
from .config import load_settings
from .filters import (
    country_allowed,
    get_client_status,
    passes_bid_remaining,
    passes_budget,
    passes_client_history,
    passes_currency,
    passes_keyword_blocklist,
    passes_payment_verified,
    passes_recency,
    passes_skill_blocklist,
    passes_upgrades,
)
from .db import (
    bid_exists_for_project,
    connect,
    count_bids_today,
    get_project,
    init_db,
    insert_bid,
    insert_webhook_event,
    record_sent_message,
    set_project_filtered,
    set_project_score_and_status,
    update_webhook_event,
    upsert_project,
)
from .freelancer_client import make_session
from .proposal_ai import (
    ai_filter_project,
    ai_price_and_duration,
    build_proposal_input,
    generate_proposal_openai,
)
from .scorer import score_project
from .telegram_notify import build_alert_keyboard, build_project_notification, send_telegram_message
from .telegram_notify import _project_link
from .util import safe_get


def _choose_bid_amount(budget_min: float | None, budget_max: float | None) -> float:
    if budget_min is None and budget_max is None:
        return 200.0
    if budget_max is None:
        return float(budget_min)
    if budget_min is None:
        return float(budget_max)
    return float((budget_min + budget_max) / 2)


def _choose_period_days(budget_min: float | None, budget_max: float | None) -> int:
    """Delivery period: 1 day for small projects (max budget < 300), else 7 days."""
    max_budget = budget_max if budget_max is not None else budget_min
    if max_budget is not None and float(max_budget) < 300:
        return 1
    return 7


def choose_bid_from_rules(
    currency: str | None,
    budget_min: float | None,
    budget_max: float | None,
    rules: list[dict],
) -> tuple[float, int] | None:
    """First matching bid rule -> (amount, delivery_days), else None.

    A rule matches when the project currency is in the rule's ``currencies`` list
    (empty list = any currency) AND the project's budget falls within the rule's
    ``min``..``max`` range. The representative budget is ``budget_max`` (falling
    back to ``budget_min``). Returns None when no rule matches or the budget is
    unknown, so the caller can fall back to its heuristic.
    """
    if not rules:
        return None
    budget = budget_max if budget_max is not None else budget_min
    if budget is None:
        return None
    try:
        budget = float(budget)
    except (TypeError, ValueError):
        return None
    code = (currency or "").strip().upper()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        codes = {str(c).strip().upper() for c in (rule.get("currencies") or []) if str(c).strip()}
        if codes and code and code not in codes:
            continue
        try:
            lo = float(rule.get("min"))
            hi = float(rule.get("max"))
            bid = float(rule.get("bid"))
            delivery = int(float(rule.get("delivery")))
        except (TypeError, ValueError):
            continue
        if lo <= budget <= hi:
            return bid, max(1, delivery)
    return None


def _build_proposal(s, project: dict[str, Any]) -> str:
    questions = [
        "Do you already have designs/wireframes, or should I propose a simple UI?",
        "What’s your ideal deadline and must-have features for v1?",
    ]
    # Style-consistent fallback (no empty lines, no ";", short question at start/end).
    fallback = (
        "Hi!\n"
        f"Are you looking for someone who can deliver {project['title']} cleanly and on time?\n"
        "I have shipped similar projects before and can map your requirements to a simple, reliable plan.\n"
        "My plan, clarify the must-haves, implement v1, then test and hand over with notes.\n"
        "What is your ideal deadline?\n"
        "Best regards,\n"
        "User"
    )
    if not s.openai_api_key:
        return fallback
    try:
        return generate_proposal_openai(
            api_key=s.openai_api_key,
            model=s.openai_model,
            data=build_proposal_input(
                s,
                title=project["title"],
                description=project["description"] or "",
                budget_min=project["budget_min"],
                budget_max=project["budget_max"],
                currency=project["currency"],
                skills=project.get("skills") or "",
                questions=questions,
            ),
        )
    except Exception as exc:
        # Don't let an OpenAI outage / bad key crash the whole webhook — fall back
        # to the template so the project is still saved for review.
        print(f"[proposal] OpenAI generation failed, using template fallback: {exc}")
        return fallback


def _get_my_user_id(session) -> int:
    try:
        return int(fln_users.get_self_user_id(session))
    except Exception:
        me = fln_users.get_self(session)
        if isinstance(me, dict) and "result" in me:
            return int(me["result"]["id"])
        return int(me["id"])


def _extract_project(payload: dict[str, Any]) -> dict[str, Any]:
    src = payload.get("project") if isinstance(payload.get("project"), dict) else payload
    budget = src.get("budget") if isinstance(src.get("budget"), dict) else {}
    top_currency = src.get("currency")
    if isinstance(top_currency, dict):
        currency = top_currency.get("code")
    else:
        currency = top_currency
    if not currency and isinstance(budget.get("currency"), dict):
        currency = (budget.get("currency") or {}).get("code")
    bid_stats = src.get("bid_stats") if isinstance(src.get("bid_stats"), dict) else {}

    project_id = src.get("id", src.get("project_id"))
    if project_id is None:
        raise ValueError("Webhook payload missing project id")

    return {
        "id": int(project_id),
        "title": src.get("title") or payload.get("title") or "",
        "url": src.get("seo_url") or src.get("url"),
        "description": src.get("description") or payload.get("description") or "",
        "currency": currency,
        "budget_min": safe_get(budget, "minimum", src.get("budget_min")),
        "budget_max": safe_get(budget, "maximum", src.get("budget_max")),
        "bid_count": safe_get(bid_stats, "bid_count", src.get("bid_count")),
        "bid_avg": safe_get(bid_stats, "bid_avg", src.get("bid_avg")),
        "skills": ",".join([str(s.get("name")) for s in (src.get("jobs") or []) if isinstance(s, dict)]),
        "created_at": src.get("time_submitted") or src.get("date_submitted") or src.get("time_created") or payload.get("created_at"),
        "bidperiod": src.get("bidperiod"),
        "owner_id": src.get("owner_id") or src.get("user_id") or payload.get("owner_id") or payload.get("user_id"),
        # Project upgrade flags (NDA, pf_only, sealed, ...) for the upgrade filter.
        "upgrades": src.get("upgrades"),
        # Keep the full source payload so every available API field is inspectable.
        "raw_json": json.dumps(src, ensure_ascii=True, sort_keys=True, default=str),
    }


def _fetch_project_detail_snapshot(session, project_id: int) -> dict[str, Any] | None:
    try:
        # Request full_description so the snapshot holds the whole project text,
        # not the truncated 100-char preview the lean payload returns.
        return fln_projects.get_project_by_id(
            session,
            int(project_id),
            project_details={"full_description": True, "job_details": True},
        )
    except Exception as exc:
        return {"_fetch_error": str(exc), "project_id": int(project_id)}


def _detail_full_description(project_detail: dict[str, Any] | None) -> str | None:
    """Pull the full description out of a get_project_by_id response, if present."""
    if not isinstance(project_detail, dict):
        return None
    src = project_detail.get("result") if isinstance(project_detail.get("result"), dict) else project_detail
    desc = src.get("description") if isinstance(src, dict) else None
    return desc if isinstance(desc, str) and desc.strip() else None


def _detail_skills(project_detail: dict[str, Any] | None) -> str | None:
    """Pull the skill badges (jobs) out of a get_project_by_id response, if present."""
    if not isinstance(project_detail, dict):
        return None
    src = project_detail.get("result") if isinstance(project_detail.get("result"), dict) else project_detail
    jobs = src.get("jobs") if isinstance(src, dict) else None
    if not isinstance(jobs, list):
        return None
    names = [str(j.get("name")) for j in jobs if isinstance(j, dict) and j.get("name")]
    return ",".join(names) if names else None


def process_webhook_payload(payload: dict[str, Any], delay_seconds: int | None = None, dry_run_override: bool | None = None) -> dict[str, Any]:
    s = load_settings()
    dry_run = s.dry_run if dry_run_override is None else dry_run_override
    delay = s.webhook_delay_seconds if delay_seconds is None else delay_seconds

    conn = connect()
    init_db(conn)
    session = make_session(s.fln_oauth_token, s.fln_url)
    payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    event_id = insert_webhook_event(conn, "received", payload_json=payload_json)

    def _notify_telegram(project: dict[str, Any], client_status: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
        if not s.notify_enabled or not s.telegram_targets:
            return None
        # Outside the notification window: skip the alert. Unlike the polling path a
        # webhook is one-shot (the project is marked handled), so there is nothing to
        # defer to — the job is still collected and browsable on the Jobs page.
        if not s.notify_window_open():
            return {"sent": False, "skipped": "outside notification window"}
        message = build_project_notification(project=project, client_status=client_status, result=result)
        keyboard = build_alert_keyboard(_project_link(project), project.get("id"))
        sent_any = False
        last_err: str | None = None
        for token, chat_id in s.telegram_targets:
            try:
                tg_resp = send_telegram_message(token, chat_id, message, reply_markup=keyboard)
                # Record per-chat so each recipient's Mark-read button works. NOTE:
                # the telegram-listener long-polls only the PRIMARY bot token, so
                # Mark-read taps on an extra bot's message won't be processed.
                message_id = (tg_resp.get("result") or {}).get("message_id") if isinstance(tg_resp, dict) else None
                if message_id is not None:
                    record_sent_message(conn, int(message_id), str(chat_id), project.get("id"), message)
                sent_any = True
            except Exception as exc:
                last_err = str(exc)
                print(f"[telegram] send to {chat_id} failed: {exc}")
        if sent_any:
            return {"sent": True, "ok": True}
        return {"sent": False, "error": last_err}

    try:
        project = _extract_project(payload)
        update_webhook_event(
            conn,
            event_id,
            project_id=int(project["id"]),
            extracted_project_json=json.dumps(project, ensure_ascii=True, sort_keys=True, default=str),
        )

        # Idempotency: a redelivered or duplicate webhook must not bid/notify again.
        # Most paths write a bid row first; the auto-apply-OFF collect path writes no
        # bid (to skip OpenAI), so we also treat an already-saved 'great' project as
        # handled. The raw event is still recorded above for audit.
        _existing = get_project(conn, int(project["id"]))
        if bid_exists_for_project(conn, int(project["id"])) or (
            _existing is not None and (_existing["status"] or "") == "great"
        ):
            result = {
                "ok": True,
                "project_id": project["id"],
                "client_status": None,
                "reason": "duplicate_already_handled",
                "duplicate": True,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        # Stale projects (created more than the recency window ago) are excluded
        # entirely: never written to the projects table and NOT notified to
        # Telegram. The webhook event itself is still recorded above.
        recency_ok, recency_reason = passes_recency(project["created_at"], s.max_project_age_seconds)
        if not recency_ok:
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": None,
                "reason": recency_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        # Skip projects whose bidding closes too soon (excluded, not notified).
        bid_ok, bid_reason = passes_bid_remaining(project, s.min_bid_remaining_seconds)
        if not bid_ok:
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": None,
                "reason": bid_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        # Skipped currencies (e.g. INR) are excluded entirely: never written to
        # the projects table and NOT notified to Telegram. The webhook event
        # itself is still recorded above for traceability.
        currency_ok, currency_reason = passes_currency(project["currency"], s.skip_currencies)
        if not currency_ok:
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": None,
                "reason": currency_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        upg_ok, upg_reason = passes_upgrades(project.get("upgrades"), s.skip_upgrades)
        if not upg_ok:
            set_project_filtered(conn, int(project["id"]), upg_reason)
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": None,
                "reason": upg_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        project_detail = _fetch_project_detail_snapshot(session, int(project["id"]))
        if project_detail is not None:
            update_webhook_event(
                conn,
                event_id,
                project_detail_json=json.dumps(project_detail, ensure_ascii=True, sort_keys=True, default=str),
            )
            # The webhook payload may carry only a truncated description; prefer the
            # full text from the detail fetch when it is longer.
            full_desc = _detail_full_description(project_detail)
            if full_desc and len(full_desc) > len(project["description"] or ""):
                project["description"] = full_desc
            # Backfill skill badges from the detail fetch if the payload had none,
            # so the cover letter is always generated from description + skills.
            if not (project.get("skills") or "").strip():
                detail_skills = _detail_skills(project_detail)
                if detail_skills:
                    project["skills"] = detail_skills

        # Negative-keyword filter: drop projects whose title/description contain a
        # blocked term. Done after the detail fetch so the description blocklist is
        # matched against the FULL text, not the truncated webhook preview. Excluded
        # entirely (like currency/recency) — not written to the projects table.
        kw_ok, kw_reason = passes_keyword_blocklist(
            project["title"], project["description"],
            s.exclude_title_keywords, s.exclude_desc_keywords,
        )
        if not kw_ok:
            result = {"ok": False, "project_id": project["id"], "client_status": None, "reason": kw_reason}
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        sk_ok, sk_reason = passes_skill_blocklist(project.get("skills"), s.exclude_skills)
        if not sk_ok:
            result = {"ok": False, "project_id": project["id"], "client_status": None, "reason": sk_reason}
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        upsert_project(conn, {**project, "status": "webhook_new", "score": 0})

        client_status = get_client_status(session, project["owner_id"])
        bids_today = count_bids_today(conn)
        # The daily cap only constrains actual auto-bidding. When auto-apply is off
        # the bot just collects + notifies, so the cap must not block that.
        if s.auto_apply and bids_today >= s.max_bids_per_day:
            result = {"ok": False, "project_id": project["id"], "client_status": client_status, "reason": "daily_cap_reached"}
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        budget_ok, budget_reason = passes_budget(
            project["budget_min"], project["budget_max"], project["currency"], s.min_budget_usd
        )
        if not budget_ok:
            set_project_filtered(conn, int(project["id"]), budget_reason)
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": client_status,
                "reason": budget_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        score_result = score_project(
            title=project["title"],
            description=project["description"] or "",
            skills_csv=project["skills"] or "",
            currency=project["currency"],
            budget_min=project["budget_min"],
            budget_max=project["budget_max"],
            min_budget_usd=s.min_budget_usd,
            required_skills=s.skills,
            min_skill_matches=s.min_skill_matches,
        )
        country_ok, country_reason = country_allowed(client_status, s.allow_countries, s.skip_countries)
        if not country_ok:
            set_project_filtered(conn, int(project["id"]), country_reason)
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": client_status,
                "reason": country_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        history_ok, history_reason = passes_client_history(client_status, s.min_client_completed_jobs)
        if not history_ok:
            set_project_filtered(conn, int(project["id"]), history_reason)
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": client_status,
                "reason": history_reason,
                "required_completed_jobs": max(0, int(s.min_client_completed_jobs)),
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        payment_ok, payment_reason = passes_payment_verified(client_status, s.require_payment_verified)
        if not payment_ok:
            set_project_filtered(conn, int(project["id"]), payment_reason)
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": client_status,
                "reason": payment_reason,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        is_great = bool(score_result.eligible and score_result.score >= s.min_score)
        status = "great" if is_great else "skipped"
        if int(score_result.score) <= 0:
            set_project_filtered(conn, int(project["id"]), "score:" + (",".join(score_result.reasons) or "zero"))
        else:
            set_project_score_and_status(conn, int(project["id"]), int(score_result.score), status)
        if not is_great:
            result = {
                "ok": False,
                "project_id": project["id"],
                "client_status": client_status,
                "reason": "project_not_eligible",
                "score": score_result.score,
                "reasons": score_result.reasons,
            }
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        # Optional natural-language AI gate (BOT_AI_FILTER_*). Only runs on great,
        # eligible projects so the OpenAI cost is bounded. Fails open.
        if s.ai_filter_enabled and s.openai_api_key and s.ai_filter_criteria.strip():
            should_bid, ai_reason = ai_filter_project(
                s.openai_api_key,
                s.openai_model,
                s.ai_filter_criteria,
                title=project["title"],
                description=project["description"] or "",
                skills=project.get("skills") or "",
                budget_min=project["budget_min"],
                budget_max=project["budget_max"],
                currency=project["currency"],
            )
            if not should_bid:
                set_project_filtered(conn, int(project["id"]), ai_reason)
                result = {
                    "ok": False,
                    "project_id": project["id"],
                    "client_status": client_status,
                    "reason": "ai_filter_reject",
                    "ai_reason": ai_reason,
                    "score": score_result.score,
                }
                update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
                return result

        # Pricing precedence: AI auto-pricing (if enabled) -> structured per-currency
        # bid rules -> budget-based heuristic. Each falls back to the next on miss.
        priced = None
        if s.ai_pricing_enabled and s.openai_api_key and s.ai_pricing_rules.strip():
            priced = ai_price_and_duration(
                s.openai_api_key,
                s.openai_model,
                s.ai_pricing_rules,
                title=project["title"],
                description=project["description"] or "",
                skills=project.get("skills") or "",
                budget_min=project["budget_min"],
                budget_max=project["budget_max"],
                currency=project["currency"],
            )
        if priced is not None:
            amount, period_days = priced
        else:
            ruled = choose_bid_from_rules(project["currency"], project["budget_min"], project["budget_max"], s.bid_rules)
            if ruled is not None:
                amount, period_days = ruled
            else:
                amount = _choose_bid_amount(project["budget_min"], project["budget_max"])
                period_days = _choose_period_days(project["budget_min"], project["budget_max"])

        # Auto-apply OFF (master switch) OR outside the auto-apply window: the bot
        # only COLLECTS the great project and notifies you — no proposal is generated
        # (so no OpenAI spend) and no draft bid is created. You apply manually from the
        # Jobs page, which generates the proposal on demand. The 'great' status is what
        # makes a redelivery idempotent. The window lets you auto-bid only during set
        # hours while still collecting (and, in-window, notifying) round the clock.
        if not s.auto_apply or not s.autoapply_window_open():
            set_project_score_and_status(conn, int(project["id"]), int(score_result.score), "great")
            result = {
                "ok": True,
                "project_id": project["id"],
                "client_status": client_status,
                "saved": True,
                "status": "great",
                "score": score_result.score,
                "reasons": score_result.reasons,
                "auto_apply": False,
            }
            tg_status = _notify_telegram(project, client_status, result)
            if tg_status is not None:
                result["telegram"] = tg_status
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        # Auto-apply ON + save-only: a high-scoring, fully eligible match. We generate
        # the proposal and SAVE it as a draft bid instead of placing a real bid. Query:
        #   sqlite3 bot.sqlite3 "SELECT proposal, period_days FROM bids WHERE status='webhook_proposal_saved';"
        # Set BOT_WEBHOOK_SAVE_ONLY=false to fall through to the auto-bid pipeline.
        if s.webhook_save_only:
            proposal = _build_proposal(s, project)
            insert_bid(
                conn,
                int(project["id"]),
                None,
                float(amount),
                period_days,
                s.default_milestone_percent,
                proposal,
                status="webhook_proposal_saved",
            )
            set_project_score_and_status(conn, int(project["id"]), int(score_result.score), "great")
            result = {
                "ok": True,
                "project_id": project["id"],
                "client_status": client_status,
                "saved": True,
                "status": "great",
                "score": score_result.score,
                "reasons": score_result.reasons,
                "proposal": proposal,
                "amount": float(amount),
                "period_days": period_days,
            }
            tg_status = _notify_telegram(project, client_status, result)
            if tg_status is not None:
                result["telegram"] = tg_status
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        if delay > 0:
            time.sleep(delay)

        proposal = _build_proposal(s, project)
        if dry_run:
            insert_bid(
                conn,
                int(project["id"]),
                None,
                float(amount),
                period_days,
                s.default_milestone_percent,
                proposal,
                status="webhook_dry_run",
            )
            set_project_score_and_status(conn, int(project["id"]), int(score_result.score), "webhook_bid_dry_run")
            result = {
                "ok": True,
                "project_id": project["id"],
                "client_status": client_status,
                "dry_run": True,
                "proposal": proposal,
            }
            tg_status = _notify_telegram(project, client_status, result)
            if tg_status is not None:
                result["telegram"] = tg_status
            update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
            return result

        bidder_id = _get_my_user_id(session)
        resp = place_bid(
            session,
            BidRequest(
                project_id=int(project["id"]),
                bidder_id=bidder_id,
                description=proposal,
                amount=float(amount),
                period_days=period_days,
                milestone_percent=s.default_milestone_percent,
            ),
        )
        bid_id = None
        if isinstance(resp, dict):
            result = resp.get("result") or {}
            bid_id = result.get("id") or result.get("bid_id")
        insert_bid(
            conn,
            int(project["id"]),
            int(bid_id) if bid_id else None,
            float(amount),
            period_days,
            s.default_milestone_percent,
            proposal,
            status="webhook_sent",
        )
        set_project_score_and_status(conn, int(project["id"]), int(score_result.score), "webhook_bid_sent")
        result = {"ok": True, "project_id": project["id"], "client_status": client_status, "dry_run": False, "bid_id": bid_id}
        tg_status = _notify_telegram(project, client_status, result)
        if tg_status is not None:
            result["telegram"] = tg_status
        update_webhook_event(conn, event_id, result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
        return result
    except Exception as exc:
        error_result = {"ok": False, "error": str(exc)}
        update_webhook_event(conn, event_id, result_json=json.dumps(error_result, ensure_ascii=True, sort_keys=True, default=str))
        raise


def process_webhook_file(path: str, delay_seconds: int | None = None, dry_run_override: bool | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Payload file must contain a JSON object")
    return process_webhook_payload(payload, delay_seconds=delay_seconds, dry_run_override=dry_run_override)


def serve_webhook(host: str, port: int, dry_run_override: bool | None = None, delay_seconds: int | None = None) -> None:
    settings = load_settings()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status: int, body: dict[str, Any]) -> None:
            response = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def do_POST(self) -> None:  # noqa: N802
            if settings.webhook_secret:
                incoming = self.headers.get("X-Webhook-Secret")
                if incoming != settings.webhook_secret:
                    self._respond(401, {"ok": False, "error": "unauthorized"})
                    return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
            except Exception as exc:
                self._respond(400, {"ok": False, "error": str(exc)})
                return

            # Acknowledge immediately so the sender does not time out and retry
            # while we do the slow work (detail fetch, scoring, optional delay,
            # bid placement, Telegram send). ThreadingHTTPServer gives this
            # request its own thread, so processing here does not block others.
            self._respond(200, {"ok": True, "queued": True})
            try:
                process_webhook_payload(payload, delay_seconds=delay_seconds, dry_run_override=dry_run_override)
            except Exception as exc:
                print(f"[webhook] processing error: {exc}")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            print(f"[webhook] {self.address_string()} - {format % args}")

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Webhook listening on http://{host}:{port}/webhook")
    server.serve_forever()
