from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError


def _parse_epoch(value: Any) -> float | None:
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


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m ago"
    return f"{s // 86400}d {(s % 86400) // 3600}h ago"


def _format_posted(created_at: Any, now_epoch: float | None = None) -> str | None:
    ts = _parse_epoch(created_at)
    if ts is None:
        return None
    now = now_epoch if now_epoch is not None else time.time()
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return f"{dt:%Y-%m-%d %H:%M} UTC ({_ago(now - ts)})"


def _esc(text: Any) -> str:
    s = "" if text is None else str(text)
    # Telegram HTML parse mode escaping
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _project_link(project: dict[str, Any]) -> str | None:
    raw = project.get("url")
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s
    # `seo_url` may be just a slug/path from Freelancer API
    if s.startswith("/"):
        return f"https://www.freelancer.com{s}"
    return f"https://www.freelancer.com/projects/{s}/details"


TELEGRAM_MAX_CHARS = 4096
# Leave headroom below the hard limit for HTML tags / escaping expansion.
_SAFE_MAX_CHARS = 3900


def _clean_description(desc: str) -> str:
    """Trim and collapse excessive blank lines, but keep paragraph structure."""
    s = (desc or "").strip()
    while "\n\n\n" in s:
        s = s.replace("\n\n\n", "\n\n")
    return s


def _bordered_table(cells: list[tuple[str, str]]) -> str:
    """Render a 2-row (header + values) horizontal table with box-drawing borders.

    Telegram messages support no CSS, so a real bordered ``<table>`` is impossible.
    Drawing the border with box characters inside a monospace ``<pre>`` block is
    the only way to get visible cell borders; on a dark theme they render in the
    default light text colour. The wide bordered rows also push the message bubble
    to full width. The returned string is meant to be wrapped in ``<pre>`` by the
    caller (and HTML-escaped, since values may contain ``<``/``>``/``&``).
    """
    widths = [max(len(label), len(value)) for label, value in cells]

    def _rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def _row(values: list[str]) -> str:
        return "│" + "│".join(f" {v.ljust(w)} " for v, w in zip(values, widths)) + "│"

    return "\n".join([
        _rule("┌", "┬", "┐"),
        _row([label for label, _ in cells]),
        _rule("├", "┼", "┤"),
        _row([value for _, value in cells]),
        _rule("└", "┴", "┘"),
    ])


def build_project_notification(
    *,
    project: dict[str, Any],
    client_status: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    pid = project.get("id")
    title = _esc(project.get("title") or "(no title)")
    url = _project_link(project)
    skills_raw = (project.get("skills") or "").strip()
    skills_display = _esc(skills_raw or "-")
    desc_raw = _clean_description(project.get("description") or "")
    budget_min = project.get("budget_min")
    budget_max = project.get("budget_max")
    bid_count = project.get("bid_count")
    bid_avg = project.get("bid_avg")
    score = result.get("score") if isinstance(result, dict) else None
    client_country = None
    completed_jobs = None
    payment_verified = None
    if client_status:
        client_country = client_status.get("country_name") or client_status.get("country_code") or client_status.get("country")
        completed_jobs = client_status.get("completed_jobs")
        payment_verified = client_status.get("payment_verified")

    currency_raw = (project.get("currency") or "").strip()
    posted = _format_posted(project.get("created_at"))
    pv_text = "Yes" if payment_verified is True else ("No" if payment_verified is False else "-")

    # Render the title itself as the clickable link. Telegram messages support no
    # font colour (so red/blue text via CSS is impossible) — a hyperlink is the
    # only text Telegram tints, showing it in the theme accent colour (blue on a
    # dark theme), which is how the title gets highlighted. The full URL is also
    # kept on its own "Link:" line below so it stays visible and easy to copy/open.
    if url:
        safe_url = _esc(url)
        title_line = f"<b>Title:</b> <a href=\"{safe_url}\">{title}</a>"
    else:
        title_line = f"<b>Title:</b> {title}"
    head_lines: list[str] = [f"#{_esc(pid)}", title_line]
    if url:
        head_lines.append(f"Link: <a href=\"{safe_url}\">{safe_url}</a>")
    skills_line = f"<b>Skills:</b> {skills_display}"
    head_lines.append(skills_line)

    # Metadata as a horizontal table: a header row of column names above a single
    # row of values, column-aligned in a monospace <pre> block. The <pre> block
    # also forces Telegram to render the message bubble at full width.
    cells: list[tuple[str, str]] = []
    if budget_min is not None or budget_max is not None:
        cells.append(("Budget", f"{budget_min} - {budget_max} {currency_raw}".rstrip()))
    if score is not None:
        cells.append(("Score", str(int(score))))
    if posted:
        cells.append(("Posted", posted))
    cells.append(("Bid count", str(bid_count) if bid_count is not None else "-"))
    cells.append(("Bid avg", str(round(float(bid_avg), 2)) if bid_avg is not None else "-"))
    cells.append(("Country", str(client_country) if client_country else "-"))
    cells.append(("Completed", str(completed_jobs) if completed_jobs is not None else "-"))
    cells.append(("Verified", pv_text))

    table = "<pre>" + _esc(_bordered_table(cells)) + "</pre>"
    header = "\n".join(head_lines) + "\n" + table

    if not desc_raw and not skills_raw:
        return header

    # Full description inside an EXPANDABLE blockquote: Telegram collapses it to a
    # few lines with a tap-to-expand control, so the WHOLE text is available
    # without the message taking over the chat. We send the entire description and
    # only trim if the assembled message would breach Telegram's hard 4096-char
    # limit (see _SAFE_MAX_CHARS for the headroom we keep below it).

    def _assemble(desc_text: str) -> str:
        parts = [header]
        if desc_text:
            parts.append("")
            parts.append("<b>Description:</b>")
            parts.append(f"<blockquote expandable>{_esc(desc_text)}</blockquote>")
        return "\n".join(parts)

    msg = _assemble(desc_raw)
    if desc_raw and len(msg) > _SAFE_MAX_CHARS:
        # Drop just enough description to fit, accounting for HTML-escape growth in
        # the remaining text (small safety margin), then mark the truncation.
        overflow = len(msg) - _SAFE_MAX_CHARS
        keep = max(0, len(desc_raw) - overflow - 16)
        msg = _assemble(desc_raw[:keep].rstrip() + " …")

    return msg


READ_PREFIX = "✅ <b>READ</b>\n"


def build_alert_keyboard(project_url: str | None, project_id: Any, read: bool = False) -> dict[str, Any]:
    """Inline keyboard for an alert: an Open-link button plus a Mark-read button.

    The Mark-read button carries ``read:<project_id>`` as callback_data; the
    telegram listener handles the tap. Once read, it is replaced by a static
    "✅ Read" marker so the buttons themselves show the state too.
    """
    rows: list[list[dict[str, Any]]] = []
    if project_url:
        rows.append([{"text": "🔗 Open", "url": project_url}])
    if read:
        rows.append([{"text": "✅ Read", "callback_data": "noop"}])
    else:
        rows.append([{"text": "✅ Mark read", "callback_data": f"read:{project_id}"}])
    return {"inline_keyboard": rows}


class TelegramAPIError(RuntimeError):
    """A Telegram API call failed. Carries enough detail to decide on a retry.

    ``status`` is the HTTP status (e.g. 429, 400, 500) or ``None`` for a transport
    failure; ``retry_after`` is the seconds Telegram asks us to wait on a 429;
    ``is_network`` flags a connection/DNS error (always worth retrying).
    """

    def __init__(self, message: str, *, status: int | None = None,
                 retry_after: float | None = None, is_network: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.is_network = is_network


def _telegram_api(bot_token: str, method: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        retry_after = None
        try:
            retry_after = (json.loads(body).get("parameters") or {}).get("retry_after")
        except Exception:
            pass
        raise TelegramAPIError(f"Telegram HTTP {exc.code}: {body}", status=exc.code, retry_after=retry_after) from exc
    except URLError as exc:
        raise TelegramAPIError(f"Telegram request failed: {exc}", is_network=True) from exc


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text_html: str,
    disable_web_page_preview: bool = True,
    reply_markup: dict[str, Any] | None = None,
    max_retries: int = 4,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    # Retry transient failures so a rate-limit (429) or network blip doesn't
    # silently drop an alert. Non-retryable client errors (e.g. 400 bad HTML)
    # are raised immediately — retrying them would never succeed.
    attempt = 0
    while True:
        try:
            return _telegram_api(bot_token, "sendMessage", payload, timeout=10)
        except TelegramAPIError as exc:
            retryable = exc.is_network or exc.status == 429 or (exc.status is not None and exc.status >= 500)
            attempt += 1
            if not retryable or attempt > max_retries:
                raise
            if exc.status == 429 and exc.retry_after:
                wait = float(exc.retry_after) + 0.5  # honour Telegram's back-off
            else:
                wait = min(30.0, 2.0 ** attempt)  # exponential back-off, capped
            time.sleep(wait)


def get_updates(bot_token: str, offset: int, timeout: int = 50) -> dict[str, Any]:
    """Long-poll for bot updates (callback button presses). ``offset`` is the next
    update_id to fetch; pass last_update_id + 1 to ack everything before it."""
    payload = {"offset": offset, "timeout": timeout, "allowed_updates": ["callback_query"]}
    # urlopen timeout must exceed the long-poll timeout or it aborts early.
    return _telegram_api(bot_token, "getUpdates", payload, timeout=timeout + 10)


def answer_callback_query(bot_token: str, callback_query_id: str, text: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _telegram_api(bot_token, "answerCallbackQuery", payload, timeout=10)


def edit_message_text(
    bot_token: str,
    chat_id: str,
    message_id: int,
    text_html: str,
    reply_markup: dict[str, Any] | None = None,
    disable_web_page_preview: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_web_page_preview,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _telegram_api(bot_token, "editMessageText", payload, timeout=10)
