from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)

def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

def _csv(name: str) -> list[str]:
    v = os.getenv(name, "")
    return [x.strip() for x in v.split(",") if x.strip()]

@dataclass(frozen=True)
class Settings:
    # Freelancer
    fln_oauth_token: str
    fln_url: str | None

    # Bot
    dry_run: bool
    max_bids_per_day: int
    poll_interval_seconds: int
    min_score: int
    min_budget_usd: int
    min_skill_matches: int
    keywords: list[str]
    skills: list[str]
    allow_countries: list[str]
    skip_countries: list[str]
    skip_currencies: list[str]
    min_client_completed_jobs: int
    require_payment_verified: bool
    max_project_age_seconds: int
    min_bid_remaining_seconds: int

    # Bid defaults
    default_period_days: int
    default_milestone_percent: int

    # OpenAI
    openai_api_key: str | None
    openai_model: str

    # Webhook
    webhook_secret: str | None
    webhook_delay_seconds: int
    webhook_save_only: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None

def load_settings() -> Settings:
    token = os.getenv("FLN_OAUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing FLN_OAUTH_TOKEN. Put it in your .env")

    return Settings(
        fln_oauth_token=token,
        fln_url=os.getenv("FLN_URL"),
        dry_run=_get_bool("BOT_DRY_RUN", True),
        max_bids_per_day=_get_int("BOT_MAX_BIDS_PER_DAY", 20),
        poll_interval_seconds=_get_int("BOT_POLL_INTERVAL_SECONDS", 300),
        min_score=_get_int("BOT_MIN_SCORE", 70),
        min_budget_usd=_get_int("BOT_MIN_BUDGET_USD", 200),
        min_skill_matches=_get_int("BOT_MIN_SKILL_MATCHES", 2),
        keywords=_csv("BOT_KEYWORDS"),
        skills=_csv("BOT_SKILLS"),
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
        default_period_days=_get_int("BOT_DEFAULT_PERIOD_DAYS", 7),
        default_milestone_percent=_get_int("BOT_DEFAULT_MILESTONE_PERCENT", 50),
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
    )
