from __future__ import annotations

import json
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)

def _get_json_list(name: str) -> list[dict]:
    """Parse an env var holding a JSON array of objects; [] on empty/invalid."""
    v = os.getenv(name)
    if not v or not v.strip():
        return []
    try:
        data = json.loads(v)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []

def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

def _csv(name: str) -> list[str]:
    v = os.getenv(name, "")
    return [x.strip() for x in v.split(",") if x.strip()]

def _get_text(name: str) -> str:
    """Read a (possibly multi-line) text value.

    The settings UI stores textarea values with newlines escaped as the literal
    two characters ``\\n`` so the single-line .env format stays intact. We decode
    them back to real newlines here.
    """
    return (os.getenv(name) or "").replace("\\n", "\n").strip()

def _get_lines(name: str) -> list[str]:
    """A textarea value parsed into a list of non-empty trimmed lines."""
    return [ln.strip() for ln in _get_text(name).splitlines() if ln.strip()]

@dataclass(frozen=True)
class Settings:
    # Freelancer
    fln_oauth_token: str
    fln_url: str | None

    # Bot
    dry_run: bool
    # Master switch for AUTOMATIC bidding. When False, the bot never auto-submits a
    # bid (the webhook path is forced to save-only); manual web-UI Apply/Auto-bid
    # are unaffected. Lets you stop/run auto-applying without touching other knobs.
    auto_apply: bool
    max_bids_per_day: int
    poll_interval_seconds: int
    min_score: int
    min_budget_usd: int
    min_skill_matches: int
    keywords: list[str]
    skills: list[str]
    exclude_title_keywords: list[str]
    exclude_desc_keywords: list[str]
    exclude_skills: list[str]
    allow_countries: list[str]
    skip_countries: list[str]
    skip_currencies: list[str]
    min_client_completed_jobs: int
    require_payment_verified: bool
    max_project_age_seconds: int
    min_bid_remaining_seconds: int

    # Active-hours window (local time, "HH:MM"); empty = always active.
    active_start: str | None
    active_end: str | None

    # Bid defaults
    default_period_days: int
    default_milestone_percent: int
    # Per-currency/budget bid rules: list of
    #   {"currencies": [...], "min": n, "max": n, "bid": n, "delivery": n}
    bid_rules: list[dict]

    # Extra free-text instructions appended to the OpenAI proposal prompt.
    proposal_instructions: str

    # --- Proposal personalization ---
    # Identity used in proposals. Empty values fall back to the built-in defaults
    # baked into proposal_ai.py, so behaviour is unchanged until these are set.
    signature_name: str
    portfolio_urls: list[str]
    profile_bullets: list[str]
    # Base style/template the model imitates. Empty = built-in STYLE_EXAMPLE.
    proposal_template: str
    # Toggles mirroring the FABB "AI Configs" checkboxes.
    include_name: bool
    include_profile: bool
    ask_question: bool
    # Literal text wrapped around the generated body ("Hello," / "Thanks!").
    proposal_prefix: str
    proposal_suffix: str

    # --- AI project filtering (natural-language accept/reject) ---
    ai_filter_enabled: bool
    ai_filter_criteria: str

    # --- AI auto-pricing & duration (natural-language pricing rules) ---
    ai_pricing_enabled: bool
    ai_pricing_rules: str

    # When true, the polling run generates a proposal for each matching project and
    # saves it to the bids table (status 'proposal_saved') for review — no real bid.
    save_proposals: bool

    # OpenAI
    openai_api_key: str | None
    openai_model: str

    # Webhook
    webhook_secret: str | None
    webhook_delay_seconds: int
    webhook_save_only: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    # One or more chat ids (TELEGRAM_CHAT_ID may be comma-separated to notify
    # several chats). telegram_chat_id keeps the raw value for diagnostics.
    telegram_chat_ids: list[str]
    # Every (bot_token, chat_id) an alert is delivered to: the primary bot paired
    # with each of its chat ids, plus any extra bots (each with its own token).
    telegram_targets: list[tuple[str, str]]

def _telegram_targets() -> list[tuple[str, str]]:
    """Resolve every (token, chat_id) pair an alert should be delivered to.

    Primary bot (TELEGRAM_BOT_TOKEN) is paired with each id in TELEGRAM_CHAT_ID;
    each extra bot in TELEGRAM_BOTS carries its own token + chat_id. Duplicates are
    removed while preserving order."""
    targets: list[tuple[str, str]] = []
    base = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if base:
        for cid in _csv("TELEGRAM_CHAT_ID"):
            targets.append((base, cid))
    for item in _get_json_list("TELEGRAM_BOTS"):
        if not isinstance(item, dict):
            continue
        tok = str(item.get("token", "")).strip()
        cid = str(item.get("chat_id", "")).strip()
        if tok and cid:
            targets.append((tok, cid))
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def load_settings() -> Settings:
    # Re-read .env from disk on every call so edits made in the settings web UI
    # take effect on the next polling cycle without restarting the bot. override=True
    # is required because the values are already in os.environ from the import-time
    # load_dotenv() above, and python-dotenv won't replace existing keys otherwise.
    load_dotenv(override=True)

    token = os.getenv("FLN_OAUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing FLN_OAUTH_TOKEN. Put it in your .env")

    return Settings(
        fln_oauth_token=token,
        fln_url=os.getenv("FLN_URL"),
        dry_run=_get_bool("BOT_DRY_RUN", True),
        # Default OFF: the bot does not auto-apply until you turn this on.
        auto_apply=_get_bool("BOT_AUTO_APPLY", False),
        max_bids_per_day=_get_int("BOT_MAX_BIDS_PER_DAY", 20),
        poll_interval_seconds=_get_int("BOT_POLL_INTERVAL_SECONDS", 300),
        min_score=_get_int("BOT_MIN_SCORE", 70),
        min_budget_usd=_get_int("BOT_MIN_BUDGET_USD", 200),
        min_skill_matches=_get_int("BOT_MIN_SKILL_MATCHES", 2),
        keywords=_csv("BOT_KEYWORDS"),
        skills=_csv("BOT_SKILLS"),
        exclude_title_keywords=_csv("BOT_EXCLUDE_TITLE_KEYWORDS"),
        exclude_desc_keywords=_csv("BOT_EXCLUDE_DESC_KEYWORDS"),
        exclude_skills=_csv("BOT_EXCLUDE_SKILLS"),
        allow_countries=_csv("BOT_ALLOW_COUNTRIES"),
        skip_countries=_csv("BOT_SKIP_COUNTRIES"),
        skip_currencies=_csv("BOT_SKIP_CURRENCIES") or ["INR"],
        min_client_completed_jobs=_get_int("BOT_MIN_CLIENT_COMPLETED_JOBS", 1),
        # Only save/bid projects whose client has a verified payment method.
        require_payment_verified=_get_bool("BOT_REQUIRE_PAYMENT_VERIFIED", True),
        # Only keep projects created within this many seconds (default 1 hour).
        # Older projects are excluded entirely. Set 0 to disable the check.
        max_project_age_seconds=_get_int("BOT_MAX_PROJECT_AGE_SECONDS", 3600),
        # Skip projects whose bidding closes in less than this many seconds
        # (time_submitted + bidperiod - now). Default 0 = disabled.
        # 6 days 22 hours = 597600.
        min_bid_remaining_seconds=_get_int("BOT_MIN_BID_REMAINING_SECONDS", 0),
        active_start=(os.getenv("BOT_ACTIVE_START") or "").strip() or None,
        active_end=(os.getenv("BOT_ACTIVE_END") or "").strip() or None,
        default_period_days=_get_int("BOT_DEFAULT_PERIOD_DAYS", 7),
        default_milestone_percent=_get_int("BOT_DEFAULT_MILESTONE_PERCENT", 50),
        bid_rules=_get_json_list("BOT_BID_RULES"),
        proposal_instructions=_get_text("BOT_PROPOSAL_INSTRUCTIONS"),
        # Proposal personalization (empty = built-in defaults in proposal_ai.py).
        signature_name=(os.getenv("BOT_SIGNATURE_NAME") or "").strip(),
        portfolio_urls=_csv("BOT_PORTFOLIO_URLS"),
        profile_bullets=_get_lines("BOT_PROFILE_BULLETS"),
        proposal_template=_get_text("BOT_PROPOSAL_TEMPLATE"),
        include_name=_get_bool("BOT_INCLUDE_NAME", True),
        include_profile=_get_bool("BOT_INCLUDE_PROFILE", True),
        ask_question=_get_bool("BOT_ASK_QUESTION", True),
        proposal_prefix=_get_text("BOT_PROPOSAL_PREFIX"),
        proposal_suffix=_get_text("BOT_PROPOSAL_SUFFIX"),
        # AI project filtering.
        ai_filter_enabled=_get_bool("BOT_AI_FILTER_ENABLED", False),
        ai_filter_criteria=_get_text("BOT_AI_FILTER_CRITERIA"),
        # AI auto-pricing & duration.
        ai_pricing_enabled=_get_bool("BOT_AI_PRICING_ENABLED", False),
        ai_pricing_rules=_get_text("BOT_AI_PRICING_RULES"),
        # Save a generated proposal draft to the DB during polling (for testing).
        save_proposals=_get_bool("BOT_SAVE_PROPOSALS", False),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.2-mini"),
        webhook_secret=os.getenv("WEBHOOK_SECRET"),
        webhook_delay_seconds=_get_int("WEBHOOK_DELAY_SECONDS", 5),
        # When true (default), great (eligible) webhook projects are saved with
        # status 'great' and NOT auto-bid. Set BOT_WEBHOOK_SAVE_ONLY=false to run
        # the original proposal/bid pipeline for eligible projects.
        webhook_save_only=_get_bool("BOT_WEBHOOK_SAVE_ONLY", True),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        telegram_chat_ids=_csv("TELEGRAM_CHAT_ID"),
        telegram_targets=_telegram_targets(),
    )
