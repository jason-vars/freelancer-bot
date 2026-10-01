"""Slack alerts via an Incoming Webhook (SLACK_WEBHOOK_URL).

A short pointer, not the whole job: title link, ids and the key numbers, with no
description (Telegram keeps the full text). Slack webhook posts carry no buttons,
so the title link is the only action.
"""
from __future__ import annotations

import json
from typing import Any
from urllib import request

from .telegram_notify import (
    _format_posted,
    _job_type,
    _project_link,
    _ssl_context,
    _upgrade_flags,
)

class SlackError(RuntimeError):
    pass


def _mrkdwn(text: Any) -> str:
    """Escape the three characters Slack's mrkdwn treats as markup."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_message(
    *,
    project: dict[str, Any],
    client_status: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    pid = project.get("id")
    title = _mrkdwn(project.get("title") or "(no title)")
    url = _project_link(project)
    score = result.get("score") if isinstance(result, dict) else None
    currency = (project.get("currency") or "").strip()
    job_type = _job_type(project)

    country = completed = verified = None
    if client_status:
        country = (client_status.get("country_name") or client_status.get("country_code")
                   or client_status.get("country"))
        completed = client_status.get("completed_jobs")
        pv = client_status.get("payment_verified")
        verified = "Yes" if pv is True else ("No" if pv is False else "-")

    lines = [f"*<{url}|{title}>*" if url else f"*{title}*", f"`#{_mrkdwn(pid)}`"]
    if job_type == "hourly":
        lines.append("⏱️ Hourly")
    elif job_type == "fixed":
        lines.append("\U0001f4b2 Fixed-price")

    facts: list[str] = []
    bmin, bmax = project.get("budget_min"), project.get("budget_max")
    if bmin is not None or bmax is not None:
        budget = f"{bmin} - {bmax} {currency}".rstrip() + (" /hr" if job_type == "hourly" else "")
        facts.append(f"*Budget:* {_mrkdwn(budget)}")
    if score is not None:
        facts.append(f"*Score:* {int(score)}")
    posted = _format_posted(project.get("created_at"))
    if posted:
        facts.append(f"*Posted:* {_mrkdwn(posted)}")
    if project.get("bid_count") is not None:
        facts.append(f"*Bids:* {_mrkdwn(project['bid_count'])}")
    if country:
        facts.append(f"*Country:* {_mrkdwn(country)}")
    if completed is not None:
        facts.append(f"*Completed:* {_mrkdwn(completed)}")
    if verified:
        facts.append(f"*Verified:* {verified}")
    if facts:
        lines.append(" · ".join(facts))

    skills = (project.get("skills") or "").strip()
    if skills:
        lines.append(f"*Skills:* {_mrkdwn(skills)}")
    flags = _upgrade_flags(project)
    if flags:
        lines.append(f"*Flags:* \U0001f3f7️ {_mrkdwn(', '.join(flags))}")

    # No description on purpose: the Slack post is a short pointer, and the full
    # text is one click away behind the title link (Telegram still carries it).
    return "\n".join(lines)


def send_slack_message(webhook_url: str, text: str, timeout: int = 30) -> None:
    """POST one message to a Slack Incoming Webhook. Raises SlackError on failure."""
    # Both flags are needed: unfurl_links kills the link card, unfurl_media the
    # image/thumbnail that Slack attaches with it.
    body = json.dumps({"text": text, "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    req = request.Request(webhook_url, data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            payload = resp.read().decode("utf-8", "replace").strip()
    except Exception as exc:  # urllib raises HTTPError/URLError/socket.timeout
        raise SlackError(f"Slack webhook request failed: {exc}") from exc
    # Slack answers a plain "ok"; anything else (invalid_payload, channel_not_found,
    # no_service) means the message was not posted.
    if payload.lower() != "ok":
        raise SlackError(f"Slack webhook rejected the message: {payload or '(empty response)'}")


def notify_slack(settings, project: dict[str, Any], client_status: dict[str, Any] | None,
                 result: dict[str, Any] | None) -> bool:
    """Send an alert to Slack when configured. Returns True if it was sent.

    Never raises: Slack failing must not stop the Telegram alert or the poll cycle."""
    url = (getattr(settings, "slack_webhook_url", "") or "").strip()
    if not url:
        return False
    try:
        send_slack_message(url, build_slack_message(project=project, client_status=client_status, result=result))
        return True
    except Exception as exc:
        print(f"[slack] send failed: {exc}")
        return False
