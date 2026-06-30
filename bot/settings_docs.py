"""Long-form documentation for each settings field, shown as an expandable
"details" panel under the field in the web UI.

Kept separate from webui.py (which holds the field layout) so the prose can grow
without touching the form definitions. Keyed by .env variable name; any field
without an entry simply shows its short one-line help. Unused keys are harmless,
so docs for not-yet-surfaced settings can live here in advance.
"""
from __future__ import annotations

FIELD_DOCS: dict[str, str] = {
    # ── Search ────────────────────────────────────────────────────────────────
    "BOT_KEYWORDS": (
        "Comma-separated terms sent to Freelancer's project search. These decide "
        "WHICH jobs are fetched at all — widen them to see more projects, narrow "
        "them to cut noise. Example: react,nextjs,flutter,fastapi,api,bugfix."
    ),
    "BOT_SKILLS": (
        "Your skill badges. Used by the scorer and the 'min skill matches' filter: "
        "a job's listed skills are compared against this list to gauge fit. They do "
        "NOT restrict the search (that's Keywords) — they rank/▼filter what's found. "
        "Example: javascript,react.js,python,fastapi."
    ),
    # ── Filters ───────────────────────────────────────────────────────────────
    "BOT_MIN_SCORE": (
        "The 0–100 fit score a job must reach to trigger an alert/bid. 0 = alert on "
        "everything that passed the other filters. Raise it (e.g. 70) once you trust "
        "the scoring and want only strong matches. Default 70."
    ),
    "BOT_MIN_BUDGET_USD": (
        "Minimum project budget in USD-equivalent. Jobs whose max budget is below "
        "this are dropped. 0 disables the check. Default 200."
    ),
    "BOT_MIN_SKILL_MATCHES": (
        "How many of your Skills must appear in the job's skill list for it to count "
        "as a match. Higher = stricter relevance. Default 2."
    ),
    "BOT_EXCLUDE_TITLE_KEYWORDS": (
        "Negative keywords matched against the job TITLE (case-insensitive substring). "
        "Any hit drops the job. Use for niches you never want, e.g. wordpress,casino. "
        "Blank disables it."
    ),
    "BOT_EXCLUDE_DESC_KEYWORDS": (
        "Same as the title blocklist but matched against the DESCRIPTION. In webhook "
        "mode it checks the full description (fetched after the preview). Blank "
        "disables it."
    ),
    "BOT_EXCLUDE_SKILLS": (
        "Skill badges you never want. If a project is tagged with any of these "
        "(case-insensitive, whole-badge or substring), it's dropped. Use for stacks "
        "you don't do, e.g. wordpress, php, .net. Blank disables it."
    ),
    "BOT_SKIP_CURRENCIES": (
        "Currency codes to drop entirely (never stored, never alerted). Common use is "
        "INR. Comma-separated, case-insensitive. Blank = keep all currencies."
    ),
    "BOT_MAX_PROJECT_AGE_SECONDS": (
        "Only keep projects posted within this many seconds. Bidding early matters, so "
        "1 hour (3600) is a sensible cap. 0 disables the recency check. Re-checked at "
        "notify time, not just at fetch."
    ),
    "BOT_MIN_BID_REMAINING_SECONDS": (
        "Skip jobs whose bidding window closes within this many seconds "
        "(time_submitted + bidperiod − now). 0 = off. 6 days 22 hours = 597600."
    ),
    "BOT_MIN_CLIENT_COMPLETED_JOBS": (
        "Require the client to have at least this many completed jobs. Only works in "
        "the WEBHOOK path (the polling search has no owner id, so the client's history "
        "is unknown). Keep at 0 for polling. Default 1."
    ),
    "BOT_REQUIRE_PAYMENT_VERIFIED": (
        "On = only clients with a verified payment method pass. Webhook-path only "
        "(needs the client profile). Reduces non-paying leads but can hide brand-new "
        "clients."
    ),
    "BOT_ALLOW_COUNTRIES": (
        "Allow-list of client countries (name or ISO code). If set, ONLY these "
        "countries pass. Webhook-path only. Blank = any country. Mutually reinforcing "
        "with the skip list."
    ),
    "BOT_SKIP_COUNTRIES": (
        "Block-list of client countries (name or ISO code). Matching clients are "
        "dropped. Webhook-path only. Blank = block none."
    ),
    # ── Bot behaviour ─────────────────────────────────────────────────────────
    "BOT_AUTO_APPLY": (
        "Master start/stop for AUTOMATIC bidding. Off (default) = the bot never "
        "submits a bid on its own — it collects jobs, generates/saves draft "
        "proposals, and notifies you, and you apply manually from the Jobs page. "
        "On = the webhook path may auto-bid eligible projects (still subject to Dry "
        "run, the daily cap, and Save-only). The web-UI Apply/Auto-bid buttons are "
        "manual and always work regardless of this switch."
    ),
    "BOT_NOTIFY_ENABLED": (
        "Master on/off for Telegram alerts. On (default) = matching jobs are pushed to "
        "your Telegram chat(s). Off = no alerts are sent, but the bot keeps fetching and "
        "storing jobs so you can still browse them on the Jobs page. Mutes notifications "
        "WITHOUT clearing your bot token or chat id, so flipping it back on resumes "
        "instantly. Takes effect on the next polling cycle / webhook event."
    ),
    "BOT_DRY_RUN": (
        "The safety switch. On = the bot NEVER places a real bid; it only saves drafts "
        "and sends alerts. Leave it On until you have reviewed proposals and pricing "
        "and are ready to bid for real. Default On."
    ),
    "BOT_MAX_BIDS_PER_DAY": (
        "Hard cap on real bids placed in a calendar day (UTC). Protects against runaway "
        "bidding and respects Freelancer limits. Default 20."
    ),
    "BOT_POLL_INTERVAL_SECONDS": (
        "Seconds between polling cycles in run-loop. Lower = fresher (bid sooner) but "
        "more API calls; too low risks rate limits. 60 is aggressive-but-fine; 300 is "
        "the default."
    ),
    "BOT_ACTIVE_START": (
        "Start of the daily active window in LOCAL time (HH:MM). Outside the window the "
        "polling loop skips cycles (no fetch, no alert). Leave both start and end blank "
        "for 24/7. Pairs with 'Active until'."
    ),
    "BOT_ACTIVE_END": (
        "End of the active window (HH:MM, local). Overnight windows work: 22:00 → 06:00 "
        "means active across midnight. start == end is treated as 24h."
    ),
    # ── AI proposal ───────────────────────────────────────────────────────────
    "BOT_PROPOSAL_INSTRUCTIONS": (
        "Free-text steering added to EVERY AI proposal as high-priority guidance (the "
        "strict format rules still win). Use it to emphasise timelines, a stack, or a "
        "tone. Multi-line is fine — it's stored safely in .env."
    ),
    # ── Bid defaults ──────────────────────────────────────────────────────────
    "BOT_SAVE_PROPOSALS": (
        "On = during polling, generate a proposal for each matching project and save it "
        "to the DB (bids table, status 'proposal_saved') WITHOUT bidding. Lets you "
        "review real proposals before enabling bids. Idempotent (one per project)."
    ),
    "BOT_DEFAULT_PERIOD_DAYS": (
        "Delivery period (days) offered on a bid when no bid-rule row matches. Default 7."
    ),
    "BOT_DEFAULT_MILESTONE_PERCENT": (
        "Milestone percentage proposed on bids (the upfront/secured share). Default 50."
    ),
    "BOT_BID_RULES": (
        "A table mapping currency + budget range → bid amount and delivery days. The "
        "FIRST row whose currency and budget match a job wins; if none match, the "
        "budget-based default is used. Empty currency list = matches any currency."
    ),
    # ── Webhook ───────────────────────────────────────────────────────────────
    "BOT_WEBHOOK_SAVE_ONLY": (
        "On (default) = a great webhook project is scored, has its proposal generated "
        "and SAVED as a draft, and you're notified — but no bid is placed. Off = run "
        "the full auto-bid pipeline for eligible projects."
    ),
    "WEBHOOK_DELAY_SECONDS": (
        "Seconds to wait before generating the proposal / placing the bid in webhook "
        "mode. A small delay can avoid racing the client's own setup. Default 5. Only "
        "used when auto-bidding (save-only off)."
    ),
    "WEBHOOK_SECRET": (
        "Optional shared secret. If set, incoming webhook POSTs must send a matching "
        "X-Webhook-Secret header or they're rejected (401). Only relevant if you expose "
        "the webhook server; irrelevant for polling. Blank = no auth."
    ),
    # ── Integrations & secrets ────────────────────────────────────────────────
    "FLN_OAUTH_TOKEN": (
        "Your Freelancer API OAuth token — REQUIRED for everything. Get it from your "
        "Freelancer developer settings. Stored in .env; shown masked here (leave blank "
        "to keep the existing one)."
    ),
    "FLN_URL": (
        "Override the Freelancer API base URL. Leave blank for production "
        "(www.freelancer.com). Only set it to a sandbox URL Freelancer gives you for "
        "testing bids without touching the live site."
    ),
    "OPENAI_API_KEY": (
        "OpenAI key used to write proposals. Blank = the bot falls back to a built-in "
        "template (no AI). Shown masked; leave blank to keep the existing key."
    ),
    "OPENAI_MODEL": (
        "Which OpenAI model writes proposals. Mini models are cheap and fast (good for "
        "high-volume bidding); full models cost more for higher quality. Must be a model "
        "your account can access."
    ),
    "TELEGRAM_BOT_TOKEN": (
        "Token of your PRIMARY notification bot, from @BotFather. Blank = Telegram "
        "notifications are off. Each recipient must press Start on this bot once before "
        "it can message them."
    ),
    "TELEGRAM_CHAT_ID": (
        "Chat id(s) the PRIMARY bot notifies. Comma-separate to reach several people "
        "with ONE bot, e.g. 12345,67890. Find ids via "
        "https://api.telegram.org/bot<TOKEN>/getUpdates."
    ),
    "TELEGRAM_BOTS": (
        "Additional bots, each with its OWN token AND chat id. Every alert goes to the "
        "primary bot above PLUS every row here — use this when recipients must receive "
        "from different bots. Note: the 'Mark read' button is interactive only for the "
        "primary bot's messages."
    ),
    # ── Proposal personalization (surfaced as the UI grows) ───────────────────
    "BOT_SIGNATURE_NAME": "Name signed on the last line of proposals. Blank = the built-in default.",
    "BOT_PORTFOLIO_URLS": "Comma-separated portfolio links the AI may cite (and only these — it won't invent URLs).",
    "BOT_PROFILE_BULLETS": "Proof bullets about you, one per line, that the AI must use rather than inventing claims.",
    "BOT_PROPOSAL_TEMPLATE": "A sample proposal the AI imitates for tone/structure. Blank = the built-in style example.",
    "BOT_INCLUDE_NAME": "On = sign proposals with your name on the last line.",
    "BOT_INCLUDE_PROFILE": "On = include your proof bullets and portfolio references in proposals.",
    "BOT_ASK_QUESTION": "On = open and close each proposal with a short question (engagement nudge).",
    "BOT_PROPOSAL_PREFIX": "Literal text prepended verbatim to every proposal (e.g. a greeting).",
    "BOT_PROPOSAL_SUFFIX": "Literal text appended verbatim to every proposal (e.g. a sign-off).",
    "BOT_AI_FILTER_ENABLED": "On = screen each candidate job with an extra AI accept/reject call (more OpenAI cost per job).",
    "BOT_AI_FILTER_CRITERIA": "Plain-English rules the AI filter applies, e.g. 'reject crypto and gambling; prefer SaaS dashboards'.",
    "BOT_AI_PRICING_ENABLED": "On = the AI sets the bid amount and duration; takes precedence over the bid-rules table.",
    "BOT_AI_PRICING_RULES": "Plain-English pricing guidance, e.g. 'Logo = $50, 2 days; full web app = $1500, 14 days'.",
}


# Sample values shown as the input PLACEHOLDER (greyed-out, not saved) so users see
# the expected format at a glance. Used only when a field has no explicit default
# placeholder of its own. Keyed by .env variable name. Secrets and on/off toggles
# are intentionally omitted (no meaningful sample).
FIELD_EXAMPLES: dict[str, str] = {
    "BOT_KEYWORDS": "react, nextjs, flutter, fastapi, api, bugfix",
    "BOT_SKILLS": "javascript, react.js, python, fastapi, node.js",
    "BOT_MIN_SCORE": "70",
    "BOT_MIN_BUDGET_USD": "200",
    "BOT_MIN_SKILL_MATCHES": "2",
    "BOT_EXCLUDE_TITLE_KEYWORDS": "wordpress, casino, dating",
    "BOT_EXCLUDE_DESC_KEYWORDS": "gambling, crypto, adult",
    "BOT_EXCLUDE_SKILLS": "wordpress, php, .net",
    "BOT_SKIP_CURRENCIES": "INR",
    "BOT_MAX_PROJECT_AGE_SECONDS": "3600",
    "BOT_MIN_BID_REMAINING_SECONDS": "0",
    "BOT_MIN_CLIENT_COMPLETED_JOBS": "1",
    "BOT_ALLOW_COUNTRIES": "United States, Canada, GB",
    "BOT_SKIP_COUNTRIES": "India, Pakistan",
    "BOT_MAX_BIDS_PER_DAY": "20",
    "BOT_POLL_INTERVAL_SECONDS": "300",
    "BOT_ACTIVE_START": "09:00",
    "BOT_ACTIVE_END": "18:00",
    "BOT_PROPOSAL_INSTRUCTIONS": "Emphasise a 1-week delivery and ask about their deadline. Keep it under 700 characters.",
    "BOT_DEFAULT_PERIOD_DAYS": "7",
    "BOT_DEFAULT_MILESTONE_PERCENT": "50",
    "WEBHOOK_DELAY_SECONDS": "5",
    "FLN_URL": "https://www.freelancer-sandbox.com",
    "OPENAI_MODEL": "gpt-5.2-mini",
    "TELEGRAM_CHAT_ID": "12345678, 87654321",
    "BOT_SIGNATURE_NAME": "User",
    "BOT_PORTFOLIO_URLS": "https://myapp.web.app, https://example.com",
    "BOT_PROFILE_BULLETS": "Senior engineer, 10+ years shipping React/Node apps\nBuilt 5 production SaaS dashboards end to end",
    "BOT_PROPOSAL_TEMPLATE": "Dear Client,\nAre you looking for a developer who can deliver this cleanly and on time?\n...\nBest regards,\nUser",
    "BOT_PROPOSAL_PREFIX": "Hello,",
    "BOT_PROPOSAL_SUFFIX": "Thanks for reading!",
    "BOT_AI_FILTER_CRITERIA": "Reject crypto and gambling jobs. Prefer SaaS dashboards with a clear spec and a real budget.",
    "BOT_AI_PRICING_RULES": "Logo = $50, 2 days. Landing page = $300, 5 days. Full web app = $1500, 14 days.",
}
