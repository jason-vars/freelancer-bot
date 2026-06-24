from __future__ import annotations

import time
from typing import Any

from .config import load_settings
from .db import (
    connect,
    get_sent_message,
    get_state,
    init_db,
    mark_message_read,
    set_state,
    utc_now_str,
)
from .telegram_notify import (
    READ_PREFIX,
    answer_callback_query,
    build_alert_keyboard,
    edit_message_text,
    get_updates,
)

OFFSET_KEY = "tg_update_offset"


def _existing_open_url(message: dict[str, Any]) -> str | None:
    """Pull the Open-link button's url out of the message's current keyboard, so a
    read message keeps its link without us having to store it separately."""
    markup = message.get("reply_markup") or {}
    for row in markup.get("inline_keyboard", []):
        for btn in row:
            if isinstance(btn, dict) and btn.get("url"):
                return btn["url"]
    return None


def _handle_callback(conn, token: str, cq: dict[str, Any]) -> None:
    data = cq.get("data") or ""
    cq_id = cq.get("id")
    if not data.startswith("read:"):
        # 'noop' (already-read marker) or anything unexpected: just acknowledge.
        if cq_id:
            answer_callback_query(token, cq_id, "")
        return

    message = cq.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        if cq_id:
            answer_callback_query(token, cq_id, "Can't update this message")
        return

    rec = get_sent_message(conn, int(message_id))
    if rec is None:
        if cq_id:
            answer_callback_query(token, cq_id, "Unknown message")
        return
    if rec["read_at"]:
        if cq_id:
            answer_callback_query(token, cq_id, "Already marked read")
        return

    url = _existing_open_url(message)
    keyboard = build_alert_keyboard(url, rec["project_id"], read=True)
    try:
        edit_message_text(token, str(chat_id), int(message_id), READ_PREFIX + rec["html"], reply_markup=keyboard)
        mark_message_read(conn, int(message_id), utc_now_str())
    except Exception as exc:  # editing can fail if the message is too old to edit
        print(f"[tg-listen] edit failed for message {message_id}: {exc}")
        if cq_id:
            answer_callback_query(token, cq_id, "Could not mark read")
        return
    if cq_id:
        answer_callback_query(token, cq_id, "Marked as read ✅")


def run_telegram_listener() -> None:
    """Long-poll Telegram for callback-button presses and mark alerts read.

    Runs as its own process alongside the poller. Safe to stop/restart: the next
    update offset is persisted in bot_state, so presses are not reprocessed.
    """
    s = load_settings()
    if not s.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    token = s.telegram_bot_token

    conn = connect()
    init_db(conn)
    offset_raw = get_state(conn, OFFSET_KEY)
    offset = int(offset_raw) if offset_raw and offset_raw.lstrip("-").isdigit() else 0

    print("Telegram listener started (mark-read buttons).")
    while True:
        try:
            resp = get_updates(token, offset, timeout=50)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[tg-listen] getUpdates failed: {exc}")
            time.sleep(3)
            continue

        for update in resp.get("result", []):
            offset = max(offset, int(update["update_id"]) + 1)
            cq = update.get("callback_query")
            if cq:
                try:
                    _handle_callback(conn, token, cq)
                except Exception as exc:
                    print(f"[tg-listen] callback error: {exc}")
        set_state(conn, OFFSET_KEY, str(offset))
