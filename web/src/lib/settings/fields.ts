// Settings registry — the web app's copy of bot/webui.py GROUPS. Keys are the same
// names the Python worker reads from .env, so the worker can overlay app_settings
// onto its environment unchanged (bot/cloud.py).
//
// scope "user":  each user edits their own value (user_settings); the admin's value
//                is the default for users who haven't saved one.
// scope "admin": only the admin edits it (app_settings).

export type FieldKind =
  | "csv" | "int" | "bool" | "text" | "secret" | "time" | "textarea"
  | "bidrules" | "telegrambots" | "select";

export type Field = {
  key: string;
  label: string;
  kind: FieldKind;
  help?: string;
  default?: string;
  choices?: [string, string][];
};

export type Group = { title: string; scope: "user" | "admin"; fields: Field[] };

// Freelancer project "upgrade" flags the worker can skip: [env suffix, upgrades key, label].
// Mirrors SKIPPABLE_UPGRADES in bot/filters.py.
export const SKIPPABLE_UPGRADES: [string, string, string][] = [
  ["NDA", "NDA", "NDA required"],
  ["PREFERRED", "pf_only", "Preferred Freelancer only"],
  ["SEALED", "sealed", "Sealed bids"],
  ["QUALIFIED", "qualified", "Qualified / verified-freelancer required"],
  ["IP_CONTRACT", "ip_contract", "IP / ownership agreement"],
  ["NON_COMPETE", "non_compete", "Non-compete agreement"],
  ["NONPUBLIC", "nonpublic", "Private / non-public project"],
  ["FULLTIME", "fulltime", "Full-time project"],
  ["RECRUITER", "recruiter", "Recruiter project"],
];

// Badges shown on a job (superset of the skippable flags). Mirrors DISPLAY_UPGRADES.
export const DISPLAY_UPGRADES: [string, string][] = [
  ["NDA", "NDA"], ["pf_only", "Preferred"], ["sealed", "Sealed"], ["qualified", "Verified"],
  ["ip_contract", "IP"], ["non_compete", "Non-compete"], ["nonpublic", "Private"],
  ["urgent", "Urgent"], ["featured", "Featured"], ["fulltime", "Full-time"], ["recruiter", "Recruiter"],
];

export const OPENAI_MODEL_CHOICES: [string, string][] = [
  ["gpt-5.6-luna", "GPT-5.6 Luna — newest, cost-efficient, recommended for proposals"],
  ["gpt-5.6-terra", "GPT-5.6 Terra — newest, balanced quality/cost"],
  ["gpt-5.6-sol", "GPT-5.6 Sol — newest frontier, premium cost"],
  ["gpt-5.5", "GPT-5.5 — previous frontier, high cost"],
  ["gpt-5.2", "GPT-5.2 — high quality, higher cost per proposal"],
  ["gpt-5.2-mini", "GPT-5.2 mini — fast & cheap, good for high-volume bidding"],
  ["gpt-5", "GPT-5 — strong reasoning, mid-high cost"],
  ["gpt-5-mini", "GPT-5 mini — reasoning, low cost"],
  ["gpt-5-nano", "GPT-5 nano — reasoning, cheapest"],
  ["gpt-4.1", "GPT-4.1 — non-reasoning, strong instruction following, fast"],
  ["gpt-4.1-mini", "GPT-4.1 mini — non-reasoning, cheap, fast"],
  ["gpt-4.1-nano", "GPT-4.1 nano — non-reasoning, cheapest, weakest"],
  ["gpt-4o", "GPT-4o — older general model, mid cost"],
  ["gpt-4o-mini", "GPT-4o mini — older, very cheap, weak on long prompts"],
];

export const GROUPS: Group[] = [
  // ── User settings ──────────────────────────────────────────────────────────────
  {
    title: "AI proposal",
    scope: "user",
    fields: [
      { key: "BOT_PROPOSAL_INSTRUCTIONS", label: "Extra instructions for ChatGPT", kind: "textarea",
        help: "Free-text steering added to every AI proposal (e.g. emphasise timelines, mention a specific stack). These win over the built-in rules." },
      { key: "BOT_SIGNATURE_NAME", label: "Your name (signature)", kind: "text",
        help: "Name signed at the end of proposals.", default: "User" },
      { key: "BOT_PROFILE_BULLETS", label: "Profile description (one per line)", kind: "textarea",
        help: "Proof bullets about you the AI may use (it never invents others). One per line. Blank = built-in default." },
      { key: "BOT_PORTFOLIO_URLS", label: "Portfolio URLs (one per line: URL | tech, role)", kind: "textarea",
        help: "Optionally tag each link so the AI cites the most relevant per job, e.g. 'https://shop.com | React, Next, payments'. The tags are never printed." },
      { key: "BOT_INCLUDE_NAME", label: "Include my name in proposal", kind: "bool",
        help: "Sign the proposal with your name on the last line.", default: "1" },
      { key: "BOT_INCLUDE_PROFILE", label: "Include profile description", kind: "bool",
        help: "Weave your proof bullets + portfolio into the proposal.", default: "1" },
      { key: "BOT_ASK_QUESTION", label: "Ask a question in proposal", kind: "bool",
        help: "Ask 1-2 questions only when the post is genuinely unclear, otherwise close with a short plan. Off = never ask.", default: "1" },
      { key: "BOT_PROPOSAL_TEMPLATE", label: "Default template (advanced)", kind: "textarea",
        help: "A sample proposal the AI imitates for tone/structure. Blank = built-in style." },
      { key: "BOT_PROPOSAL_PREFIX", label: "Text at start", kind: "text",
        help: "Literal text put at the very top of every proposal, e.g. \"Hello,\"." },
      { key: "BOT_PROPOSAL_PREFIX_INLINE", label: "Text at start on the same line", kind: "bool",
        help: "On = the text above opens the first sentence (\"Hi, I am a senior...\"). Off = it sits on its own line.", default: "0" },
      { key: "BOT_PROPOSAL_SUFFIX", label: "Text at end (closing)", kind: "textarea",
        help: "Closing line(s) placed at the very end, just before your name." },
    ],
  },
  {
    title: "Bid defaults",
    scope: "user",
    fields: [
      { key: "BOT_AUTO_GENERATE", label: "Auto-generate proposal on open", kind: "bool",
        help: "On = opening a project page makes the userscript write the proposal straight away. Off = it waits for the ✨ Generate button / Alt+G, so browsing costs nothing.", default: "1" },
      { key: "BOT_SEAL_BIDS", label: "Seal bids (free upgrade)", kind: "bool",
        help: "The userscript ticks Freelancer's FREE 'Sealed' upgrade when it fills a bid. Paid upgrades are never touched.", default: "1" },
      { key: "BOT_DEFAULT_PERIOD_DAYS", label: "Default period (days)", kind: "int",
        help: "Delivery period offered when no rule matches.", default: "7" },
      { key: "BOT_DEFAULT_MILESTONE_PERCENT", label: "Default milestone %", kind: "int",
        help: "Milestone percentage offered.", default: "50" },
      { key: "BOT_BID_RULES", label: "Bid by currency & budget", kind: "bidrules",
        help: "First matching row wins; otherwise half-way between the budget's min and max." },
    ],
  },

  // ── Admin settings ─────────────────────────────────────────────────────────────
  {
    title: "Search — which jobs get fetched",
    scope: "admin",
    fields: [
      { key: "BOT_KEYWORDS", label: "Keywords", kind: "csv",
        help: "Comma-separated search terms the Freelancer API queries on.", default: "react,nextjs,flutter,fastapi,api,bugfix" },
      { key: "BOT_SKILLS", label: "Skills", kind: "csv",
        help: "Skill badges used for scoring / the skill-match filter.", default: "javascript,react.js,python,fastapi" },
    ],
  },
  {
    title: "Fetch schedule & alerts",
    scope: "admin",
    fields: [
      { key: "BOT_FETCH_ENABLED", label: "Fetch jobs", kind: "bool",
        help: "Off = pause fetching without stopping the worker. Takes effect on the next cycle.", default: "1" },
      { key: "BOT_POLL_INTERVAL_SECONDS", label: "Fetch every (seconds)", kind: "int",
        help: "Seconds between fetch cycles.", default: "300" },
      { key: "BOT_ACTIVE_START", label: "Fetch from", kind: "time",
        help: "Only fetch between these times (blank = 24/7)." },
      { key: "BOT_ACTIVE_END", label: "Fetch until", kind: "time",
        help: "End of the fetch window (overnight works, e.g. 22:00 → 06:00)." },
      { key: "BOT_ACTIVE_TZ_OFFSET", label: "Timezone (UTC offset)", kind: "text",
        help: "Hours from UTC the windows use, e.g. 9, -5, 5.5. Blank = the worker machine's clock." },
      { key: "BOT_NOTIFY_ENABLED", label: "Send Telegram/Slack alerts", kind: "bool",
        help: "Off = mute alerts but keep collecting jobs.", default: "1" },
      { key: "BOT_NOTIFY_START", label: "Alert from", kind: "time",
        help: "Only send alerts after this time (blank = whenever fetching)." },
      { key: "BOT_NOTIFY_END", label: "Alert until", kind: "time",
        help: "Stop sending alerts after this time. Jobs found outside the window are still collected." },
    ],
  },
  {
    title: "Filters — which jobs pass",
    scope: "admin",
    fields: [
      { key: "BOT_MIN_SCORE", label: "Min score", kind: "int", help: "Show/alert only if score ≥ this (0 = all).", default: "70" },
      { key: "BOT_MIN_BUDGET_USD", label: "Min budget", kind: "int", help: "Minimum budget (0 = any).", default: "200" },
      { key: "BOT_MIN_SKILL_MATCHES", label: "Min skill matches", kind: "int", help: "How many of the Skills must match.", default: "2" },
      { key: "BOT_EXCLUDE_TITLE_KEYWORDS", label: "Exclude if title contains", kind: "csv", help: "Case-insensitive." },
      { key: "BOT_EXCLUDE_DESC_KEYWORDS", label: "Exclude if description contains", kind: "csv", help: "Case-insensitive." },
      { key: "BOT_EXCLUDE_SKILLS", label: "Exclude skills", kind: "csv", help: "Skip projects tagged with any of these skill badges." },
      { key: "BOT_SKIP_CURRENCIES", label: "Skip currencies", kind: "csv", help: "Currency codes to drop entirely, e.g. INR.", default: "INR" },
      { key: "BOT_MAX_PROJECT_AGE_SECONDS", label: "Max project age (s)", kind: "int", help: "Only keep projects newer than N seconds (0 = any). 3600 = 1h.", default: "3600" },
      { key: "BOT_MIN_BID_REMAINING_SECONDS", label: "Min bid time left (s)", kind: "int", help: "Skip if bidding closes within N seconds (0 = off).", default: "0" },
      { key: "BOT_MIN_CLIENT_COMPLETED_JOBS", label: "Min client completed jobs", kind: "int", help: "Client history floor.", default: "1" },
      { key: "BOT_REQUIRE_PAYMENT_VERIFIED", label: "Require payment verified", kind: "bool", help: "Only payment-verified clients.", default: "1" },
      { key: "BOT_ALLOW_COUNTRIES", label: "Allow countries", kind: "csv", help: "Only these client countries (empty = any)." },
      { key: "BOT_SKIP_COUNTRIES", label: "Skip countries", kind: "csv", help: "Block these client countries." },
    ],
  },
  {
    title: "Skip by project type",
    scope: "admin",
    fields: SKIPPABLE_UPGRADES.map(([suffix, key, label]) => ({
      key: `BOT_SKIP_${suffix}`, label: `Skip: ${label}`, kind: "bool" as const,
      help: `Skip projects flagged “${label}” (upgrades.${key}).`, default: "0",
    })),
  },
  {
    title: "AI project filtering",
    scope: "admin",
    fields: [
      { key: "BOT_AI_FILTER_ENABLED", label: "Enable AI project filtering", kind: "bool",
        help: "Ask the AI to accept/reject each candidate job using the criteria below (one OpenAI call per job).", default: "0" },
      { key: "BOT_AI_FILTER_CRITERIA", label: "Bidding criteria", kind: "textarea",
        help: "Plain-English rules, e.g. \"Bid on full-stack web apps; skip native mobile or crypto.\"" },
    ],
  },
  {
    title: "AI auto-pricing & duration",
    scope: "admin",
    fields: [
      { key: "BOT_AI_PRICING_ENABLED", label: "Enable AI auto-pricing", kind: "bool",
        help: "Let the AI set bid amount + delivery days for everyone. Takes precedence over each user's bid rules.", default: "0" },
      { key: "BOT_AI_PRICING_RULES", label: "Pricing rules", kind: "textarea",
        help: "Plain-English pricing, e.g. \"Logo = $50, 2 days. Website mockup = $200, 5 days.\"" },
    ],
  },
  {
    title: "Integrations & secrets",
    scope: "admin",
    fields: [
      { key: "FLN_OAUTH_TOKEN", label: "Freelancer OAuth token", kind: "secret",
        help: "The one token that fetches jobs for everyone (and looks up projects the userscript opens)." },
      { key: "FLN_URL", label: "Freelancer base URL", kind: "text", help: "Override the API base URL (e.g. sandbox)." },
      { key: "OPENAI_API_KEY", label: "OpenAI API key", kind: "secret", help: "Used for every user's proposals." },
      { key: "OPENAI_MODEL", label: "OpenAI model", kind: "select",
        help: "Model used for proposals, AI filtering & pricing.", default: "gpt-5.2-mini", choices: OPENAI_MODEL_CHOICES },
      { key: "TELEGRAM_BOT_TOKEN", label: "Telegram bot token", kind: "secret", help: "From @BotFather (blank = no Telegram alerts)." },
      { key: "TELEGRAM_CHAT_ID", label: "Telegram chat id(s)", kind: "csv", help: "Comma-separate for several chats." },
      { key: "TELEGRAM_BOTS", label: "Extra bots (token + chat)", kind: "telegrambots",
        help: "Additional bots, each with its own token and chat id." },
      { key: "SLACK_WEBHOOK_URL", label: "Slack webhook URL", kind: "secret", help: "Blank = no Slack alerts." },
    ],
  },
];

export const ALL_FIELDS: Record<string, Field> = Object.fromEntries(
  GROUPS.flatMap((g) => g.fields.map((f) => [f.key, f])),
);

export const USER_KEYS = GROUPS.filter((g) => g.scope === "user").flatMap((g) => g.fields.map((f) => f.key));

export const BUILTIN_DEFAULTS: Record<string, string> = Object.fromEntries(
  GROUPS.flatMap((g) => g.fields.filter((f) => f.default !== undefined).map((f) => [f.key, f.default!])),
);
