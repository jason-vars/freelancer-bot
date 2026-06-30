from __future__ import annotations

import html
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode

from . import db
from .env_store import read_env, update_env
from .settings_docs import FIELD_DOCS, FIELD_EXAMPLES


@dataclass(frozen=True)
class Field:
    key: str            # .env variable name
    label: str          # human label
    kind: str           # "csv"|"int"|"bool"|"text"|"secret"|"time"|"textarea"|"bidrules"|"telegrambots"|"select"
    help: str = ""      # one-line hint
    default: str = ""   # shown as placeholder when unset
    # For kind="select": (value, label) pairs. The stored value is injected as an
    # extra "(custom)" option if absent, so a hand-set model id is never lost.
    choices: tuple[tuple[str, str], ...] = ()


# Model dropdown with cost/throughput hints (FABB-style). Verify exact pricing at
# https://openai.com/api/pricing — hints are relative guidance, not exact quotes.
OPENAI_MODEL_CHOICES: tuple[tuple[str, str], ...] = (
    ("gpt-5.2-mini", "GPT-5.2 mini — fast & cheap, best default for high-volume bidding"),
    ("gpt-5.2", "GPT-5.2 — highest quality, higher cost per proposal"),
    ("gpt-4o-mini", "GPT-4o mini — very cheap, fine for most proposals"),
    ("gpt-4o", "GPT-4o — strong general model, mid cost"),
)


# Grouped settings rendered top-to-bottom. Mirrors bot/config.py so every knob the
# bot honours is editable here. Secrets are write-only (masked, blank = keep).
GROUPS: list[tuple[str, list[Field]]] = [
    ("Search — which jobs get fetched", [
        Field("BOT_KEYWORDS", "Keywords", "csv", "Comma-separated search terms the API queries on.", "react,nextjs,flutter,fastapi,api,bugfix"),
        Field("BOT_SKILLS", "Skills", "csv", "Skill badges used for scoring / skill-match filter.", "javascript,react.js,python,fastapi"),
    ]),
    ("Filters — which jobs pass", [
        Field("BOT_MIN_SCORE", "Min score", "int", "Alert only if score ≥ this (0 = alert on all).", "70"),
        Field("BOT_MIN_BUDGET_USD", "Min budget (USD)", "int", "Minimum budget in USD (0 = any).", "200"),
        Field("BOT_MIN_SKILL_MATCHES", "Min skill matches", "int", "How many of your skills must match.", "2"),
        Field("BOT_EXCLUDE_TITLE_KEYWORDS", "Exclude if title contains", "csv", "Skip projects whose TITLE contains any of these words (case-insensitive).", ""),
        Field("BOT_EXCLUDE_DESC_KEYWORDS", "Exclude if description contains", "csv", "Skip projects whose DESCRIPTION contains any of these words (case-insensitive).", ""),
        Field("BOT_EXCLUDE_SKILLS", "Exclude skills", "csv", "Skip projects tagged with any of these skill badges (case-insensitive).", ""),
        Field("BOT_SKIP_CURRENCIES", "Skip currencies", "csv", "Currency codes to drop entirely, e.g. INR.", "INR"),
        Field("BOT_MAX_PROJECT_AGE_SECONDS", "Max project age (s)", "int", "Only keep projects newer than N seconds (0 = any). 3600 = 1h.", "3600"),
        Field("BOT_MIN_BID_REMAINING_SECONDS", "Min bid time left (s)", "int", "Skip if bidding closes within N seconds (0 = off).", "0"),
        Field("BOT_MIN_CLIENT_COMPLETED_JOBS", "Min client completed jobs", "int", "Client history floor (webhook path only).", "1"),
        Field("BOT_REQUIRE_PAYMENT_VERIFIED", "Require payment verified", "bool", "Only payment-verified clients (webhook path only).", "1"),
        Field("BOT_ALLOW_COUNTRIES", "Allow countries", "csv", "Only these client countries (empty = any).", ""),
        Field("BOT_SKIP_COUNTRIES", "Skip countries", "csv", "Block these client countries (empty = none).", ""),
    ]),
    ("Bot behaviour", [
        Field("BOT_AUTO_APPLY", "Auto-apply (auto-bid)", "bool",
              "Master switch for AUTOMATIC bidding. OFF = bot only collects + notifies; you apply manually. ON = the webhook path can auto-bid. Manual Apply/Auto-bid buttons always work.", "0"),
        Field("BOT_NOTIFY_ENABLED", "Send notifications", "bool",
              "ON = send Telegram alerts for matching jobs. OFF = mute alerts but keep collecting jobs (still visible in the Jobs page). Your Telegram token/chat stay saved.", "1"),
        Field("BOT_DRY_RUN", "Dry run", "bool", "ON = never place real bids (save drafts only).", "1"),
        Field("BOT_MAX_BIDS_PER_DAY", "Max bids / day", "int", "Daily bid cap.", "20"),
        Field("BOT_POLL_INTERVAL_SECONDS", "Poll interval (s)", "int", "Seconds between polling cycles.", "300"),
        Field("BOT_ACTIVE_START", "Active from", "time", "Only run between these local times (blank = 24/7).", ""),
        Field("BOT_ACTIVE_END", "Active until", "time", "End of the active window (supports overnight, e.g. 22:00→06:00).", ""),
    ]),
    ("AI proposal", [
        Field("BOT_PROPOSAL_INSTRUCTIONS", "Extra instructions for ChatGPT", "textarea",
              "Free-text steering added to every AI proposal (e.g. emphasise timelines, mention a specific stack).", ""),
        Field("BOT_SIGNATURE_NAME", "Your name (signature)", "text",
              "Name signed at the end of proposals. Blank = built-in default.", "User"),
        Field("BOT_PROFILE_BULLETS", "Profile description (one per line)", "textarea",
              "Proof bullets about you the AI may use (never invents others). One per line. Blank = built-in default.", ""),
        Field("BOT_PORTFOLIO_URLS", "Portfolio URLs", "csv",
              "Comma-separated links the AI may reference (never invents others). Blank = built-in default.", ""),
        Field("BOT_INCLUDE_NAME", "Include my name in proposal", "bool",
              "ON = sign the proposal with your name on the last line.", "1"),
        Field("BOT_INCLUDE_PROFILE", "Include profile description", "bool",
              "ON = weave your proof bullets + portfolio into the proposal.", "1"),
        Field("BOT_ASK_QUESTION", "Ask a question in proposal", "bool",
              "ON = open and close with a short question to the client.", "1"),
        Field("BOT_PROPOSAL_TEMPLATE", "Default template (advanced)", "textarea",
              "A sample proposal the AI imitates for tone/structure. Blank = built-in style example.", ""),
        Field("BOT_PROPOSAL_PREFIX", "Text at start", "text",
              "Literal text prepended to every proposal, e.g. \"Hello,\".", ""),
        Field("BOT_PROPOSAL_SUFFIX", "Text at end", "text",
              "Literal text appended to every proposal, e.g. \"Thanks!\".", ""),
    ]),
    ("AI project filtering", [
        Field("BOT_AI_FILTER_ENABLED", "Enable AI project filtering", "bool",
              "ON = ask the AI to accept/reject each candidate job using your criteria below (adds an OpenAI call per job).", "0"),
        Field("BOT_AI_FILTER_CRITERIA", "My bidding criteria", "textarea",
              "Plain-English rules, e.g. \"Bid on full-stack web apps; skip anything needing native mobile or crypto.\"", ""),
    ]),
    ("AI auto-pricing & duration", [
        Field("BOT_AI_PRICING_ENABLED", "Enable AI auto-pricing", "bool",
              "ON = let the AI set bid amount + delivery days from your rules below. Takes precedence over the bid-rules table.", "0"),
        Field("BOT_AI_PRICING_RULES", "My pricing rules", "textarea",
              "Plain-English pricing, e.g. \"Logo = $50, 2 days. Website mockup = $200, 5 days.\"", ""),
    ]),
    ("Bid defaults", [
        Field("BOT_SAVE_PROPOSALS", "Save proposals to DB (test)", "bool",
              "ON = during polling, generate a proposal per matching project and save it to the DB for review (no bid placed).", "0"),
        Field("BOT_DEFAULT_PERIOD_DAYS", "Default period (days)", "int", "Delivery period offered on bids (when no rule matches).", "7"),
        Field("BOT_DEFAULT_MILESTONE_PERCENT", "Default milestone %", "int", "Milestone percentage offered.", "50"),
        Field("BOT_BID_RULES", "Bid by currency & budget", "bidrules",
              "Per-currency/budget bid amounts. First matching row wins; otherwise the default heuristic is used.", ""),
    ]),
    ("Webhook", [
        Field("BOT_WEBHOOK_SAVE_ONLY", "Save only (no auto-bid)", "bool", "ON = save great projects without auto-bidding.", "1"),
        Field("WEBHOOK_DELAY_SECONDS", "Delay before bid (s)", "int", "Wait before proposal/bid in webhook mode.", "5"),
        Field("WEBHOOK_SECRET", "Webhook secret", "secret", "Shared secret required on incoming webhooks.", ""),
    ]),
    ("Integrations & secrets", [
        Field("FLN_OAUTH_TOKEN", "Freelancer OAuth token", "secret", "Required. Your Freelancer API token.", ""),
        Field("FLN_URL", "Freelancer base URL", "text", "Override API base URL (e.g. sandbox).", ""),
        Field("OPENAI_API_KEY", "OpenAI API key", "secret", "For AI proposals (blank = template fallback).", ""),
        Field("OPENAI_MODEL", "OpenAI model", "select", "Model used for proposals, AI filtering & pricing. Pricing: https://openai.com/api/pricing", "gpt-5.2-mini", OPENAI_MODEL_CHOICES),
        Field("TELEGRAM_BOT_TOKEN", "Telegram bot token", "secret", "Primary bot, from @BotFather (blank = notifications off).", ""),
        Field("TELEGRAM_CHAT_ID", "Telegram chat id(s)", "csv", "Chat id(s) for the PRIMARY bot. Comma-separate for several chats, e.g. 12345,67890.", ""),
        Field("TELEGRAM_BOTS", "Extra bots (token + chat)", "telegrambots",
              "Additional bots, each with its OWN token and chat id. Every alert is sent to the primary bot AND each of these.", ""),
    ]),
]

ALL_FIELDS: dict[str, Field] = {f.key: f for group in GROUPS for f in group[1]}
_TRUE = {"1", "true", "yes", "y", "on"}


def _is_true(value: str) -> bool:
    return value.strip().lower() in _TRUE


# Generic dynamic-table widget. Each table is a `.dynwrap` containing a <tbody>,
# an `.addrow` button and a hidden `.dyn-json` input carrying the column spec
# (data-cols) and the row JSON (value). One script wires up every such table, so
# new tables (bid rules, telegram bots, ...) need no bespoke JS.
_PAGE_JS = """
(function () {
  function ser(col, val) {
    if (col.type === 'csv') return val.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (col.type === 'number') return Number(val || 0);
    return val;
  }
  document.querySelectorAll('input.dyn-json').forEach(function (hidden) {
    var cols = [];
    try { cols = JSON.parse(hidden.getAttribute('data-cols') || '[]'); } catch (e) { cols = []; }
    var wrap = hidden.closest('.dynwrap');
    var body = wrap.querySelector('tbody');
    var addBtn = wrap.querySelector('.addrow');
    var rows = [];
    try { rows = JSON.parse(hidden.value || '[]'); } catch (e) { rows = []; }
    if (!Array.isArray(rows)) rows = [];

    function addRow(r) {
      r = r || {};
      var tr = document.createElement('tr');
      cols.forEach(function (col) {
        var td = document.createElement('td');
        var inp = document.createElement('input');
        inp.type = (col.type === 'password') ? 'password' : (col.type === 'number' ? 'number' : 'text');
        inp.className = 'cell';
        inp.setAttribute('data-key', col.key);
        if (col.ph) inp.placeholder = col.ph;
        var v = r[col.key];
        if (col.type === 'csv') v = (v || []).join(',');
        inp.value = (v === undefined || v === null) ? '' : v;
        td.appendChild(inp);
        tr.appendChild(td);
      });
      var tdDel = document.createElement('td');
      var del = document.createElement('button');
      del.type = 'button'; del.className = 'del'; del.textContent = '\\u2715';
      del.addEventListener('click', function () { tr.remove(); });
      tdDel.appendChild(del); tr.appendChild(tdDel);
      body.appendChild(tr);
    }

    rows.forEach(addRow);
    addBtn.addEventListener('click', function () { addRow({}); });

    hidden.form.addEventListener('submit', function () {
      var out = [];
      body.querySelectorAll('tr').forEach(function (tr) {
        var obj = {}, empty = true;
        cols.forEach(function (col) {
          var inp = tr.querySelector('[data-key="' + col.key + '"]');
          var raw = inp ? inp.value : '';
          if (raw !== '') empty = false;
          obj[col.key] = ser(col, raw);
        });
        if (!empty) out.push(obj);
      });
      hidden.value = JSON.stringify(out);
    });
  });
})();
"""

# Tab switching: show one settings panel at a time. Kept as its own constant so the
# (brace-heavy) JS doesn't fight the f-string used to assemble the page.
_TABS_JS = """
(function () {
  var tabs = document.querySelectorAll('.tab');
  if (!tabs.length) return;
  function activate(id) {
    document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.toggle('active', p.id === id); });
    tabs.forEach(function (t) { t.classList.toggle('active', t.getAttribute('data-target') === id); });
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function () { activate(t.getAttribute('data-target')); window.scrollTo(0, 0); });
  });
})();
"""

# Column specs for the dynamic tables.
_BID_COLS = [
    {"key": "currencies", "label": "Currency code(s)", "ph": "USD,AUD (blank = any)", "type": "csv"},
    {"key": "min", "label": "Min", "ph": "0", "type": "number"},
    {"key": "max", "label": "Max", "ph": "9999", "type": "number"},
    {"key": "bid", "label": "Bid", "ph": "150", "type": "number"},
    {"key": "delivery", "label": "Delivery", "ph": "7", "type": "number"},
]
_TG_BOT_COLS = [
    {"key": "token", "label": "Bot token", "ph": "123456:ABC-DEF...", "type": "password"},
    {"key": "chat_id", "label": "Chat id", "ph": "12345", "type": "text"},
]


def _parse_rules(raw: str) -> list[dict]:
    try:
        data = json.loads(raw) if raw and raw.strip() else []
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _dyntable_control(field_key: str, columns: list[dict], current_json: str) -> str:
    """Render a dynamic add/remove table backed by a hidden JSON input (see page JS).

    Pre-filling the hidden value server-side means a no-JS submit preserves rows.
    The column spec travels to the client in data-cols so one generic script can
    build/serialise any such table."""
    compact = json.dumps(_parse_rules(current_json), separators=(",", ":"))
    safe = html.escape(compact, quote=True)
    cols_attr = html.escape(json.dumps(columns, separators=(",", ":")), quote=True)
    headers = "".join(f"<th>{html.escape(c['label'])}</th>" for c in columns) + "<th></th>"
    return (
        '<div class="dynwrap">'
        f'<table class="bidtable"><thead><tr>{headers}</tr></thead><tbody></tbody></table>'
        '<button type="button" class="addrow">+ Add row</button>'
        f'<input type="hidden" class="dyn-json" name="{field_key}" data-cols="{cols_attr}" value="{safe}">'
        "</div>"
    )


def _render(values: dict[str, str], *, saved: bool, errors: dict[str, str]) -> str:
    def esc(x: Any) -> str:
        return html.escape("" if x is None else str(x), quote=True)

    banner = ""
    if errors:
        banner = '<div class="banner err">Fix the highlighted fields — nothing was saved.</div>'
    elif saved:
        banner = '<div class="banner ok">Saved. Changes apply on the next polling cycle.</div>'

    # Tabs: show one group at a time so the (now long) form is easy to navigate.
    # All panels live in the single <form>, so hidden ones still submit normally.
    # On a validation error, open the first group that contains an errored field.
    error_keys = set(errors)
    active_idx = next((i for i, (_t, fs) in enumerate(GROUPS) if any(f.key in error_keys for f in fs)), 0)
    sections = []
    tabs = []
    for gi, (title, fields) in enumerate(GROUPS):
        rows = []
        group_has_err = any(f.key in error_keys for f in fields)
        for f in fields:
            cur = values.get(f.key, "")
            err = errors.get(f.key)
            err_html = f'<div class="fielderr">{esc(err)}</div>' if err else ""
            # Placeholder = the field's own default, else a centralized sample value
            # so empty fields still show the expected format (greyed, not saved).
            ph = f.default or FIELD_EXAMPLES.get(f.key, "")
            if f.kind == "bool":
                on = _is_true(cur)
                control = (
                    f'<select name="{f.key}">'
                    f'<option value="1"{" selected" if on else ""}>On</option>'
                    f'<option value="0"{" selected" if not on else ""}>Off</option>'
                    f"</select>"
                )
            elif f.kind == "secret":
                # Never echo the secret back to the browser. Show whether one is set;
                # an empty submit leaves the stored value untouched.
                has = bool(cur.strip())
                ph = "•••••• set — leave blank to keep" if has else "not set"
                control = f'<input type="password" name="{f.key}" placeholder="{esc(ph)}" autocomplete="off">'
            elif f.kind == "int":
                control = f'<input type="number" name="{f.key}" value="{esc(cur)}" placeholder="{esc(ph)}">'
            elif f.kind == "time":
                control = f'<input type="time" name="{f.key}" value="{esc(cur)}">'
            elif f.kind == "textarea":
                # Stored escaped (\\n) for the single-line .env — decode back to real
                # newlines so multi-line values are editable.
                disp = cur.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
                control = f'<textarea name="{f.key}" rows="4" placeholder="{esc(ph)}">{esc(disp)}</textarea>'
            elif f.kind == "select":
                opts = list(f.choices)
                if cur and cur not in {v for v, _ in opts}:
                    opts = [(cur, f"{cur} (custom)")] + opts
                options = "".join(
                    f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(lbl)}</option>'
                    for v, lbl in opts
                )
                control = f'<select name="{f.key}">{options}</select>'
            elif f.kind == "bidrules":
                control = _dyntable_control("BOT_BID_RULES", _BID_COLS, cur)
            elif f.kind == "telegrambots":
                control = _dyntable_control("TELEGRAM_BOTS", _TG_BOT_COLS, cur)
            else:  # csv | text
                control = f'<input type="text" name="{f.key}" value="{esc(cur)}" placeholder="{esc(ph)}">'
            # Full per-field documentation in an expandable panel; falls back to the
            # short help when no long doc exists for this key.
            doc = FIELD_DOCS.get(f.key)
            doc_html = (
                f'<details class="doc"><summary>ⓘ details</summary><div>{esc(doc)}</div></details>'
                if doc else ""
            )
            rows.append(
                f'<div class="row{" rowerr" if err else ""}">'
                f'<label>{esc(f.label)}<span class="key">{esc(f.key)}</span></label>'
                f'<div class="control">{control}<div class="help">{esc(f.help)}</div>{doc_html}{err_html}</div>'
                f"</div>"
            )
        active = " active" if gi == active_idx else ""
        sections.append(f'<section id="tab{gi}" class="tab-panel{active}"><h2>{esc(title)}</h2>{"".join(rows)}</section>')
        dot = '<span class="tabdot" title="has an error"></span>' if group_has_err else ""
        tabs.append(f'<button type="button" class="tab{active}" data-target="tab{gi}">{esc(title)}{dot}</button>')

    tabbar = f'<div class="tabs">{"".join(tabs)}</div>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Freelancer Bot — Settings</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 15px/1.5 system-ui, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f1320; color: #e6e8ef; }}
  header {{ padding: 20px 24px; border-bottom: 1px solid #232a40; position: sticky; top: 0; background: #0f1320; z-index: 5; display:flex; align-items:center; justify-content:space-between; }}
  header h1 {{ font-size: 18px; margin: 0; }}
  nav a {{ color: #9aa4c0; text-decoration: none; font-size: 14px; font-weight: 600; margin-left: 18px; }}
  nav a.active, nav a:hover {{ color: #e6e8ef; }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 24px; }}
  section {{ background: #161c2c; border: 1px solid #232a40; border-radius: 12px; padding: 8px 20px 16px; margin: 0 0 18px; }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: .05em; color: #9aa4c0; margin: 16px 0 12px; }}
  .row {{ display: grid; grid-template-columns: 220px 1fr; gap: 16px; padding: 12px 0; border-top: 1px solid #1d2438; align-items: start; }}
  .row:first-of-type {{ border-top: none; }}
  label {{ font-weight: 600; display: flex; flex-direction: column; gap: 3px; }}
  .key {{ font-weight: 400; font-size: 11px; color: #5f6b8c; font-family: ui-monospace, monospace; }}
  input, select, textarea {{ width: 100%; padding: 8px 10px; border-radius: 8px; border: 1px solid #2c3550; background: #0f1320; color: #e6e8ef; font: inherit; }}
  textarea {{ resize: vertical; min-height: 70px; }}
  input:focus, select:focus, textarea:focus {{ outline: 2px solid #3b82f6; border-color: transparent; }}
  .bidtable {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; }}
  .bidtable th {{ text-align: left; font-size: 11.5px; color: #9aa4c0; font-weight: 600; padding: 4px 6px; }}
  .bidtable td {{ padding: 3px 4px; }}
  .bidtable td input {{ padding: 6px 8px; }}
  .bidtable td:nth-child(1) {{ width: 38%; }}
  .bidtable .del {{ width: auto; background: #3a1717; color: #f8a3a3; border: 1px solid #6b2626; padding: 6px 10px; border-radius: 7px; cursor: pointer; }}
  .addrow {{ background: #1d2438; color: #cdd5ee; border: 1px solid #2c3550; padding: 7px 14px; border-radius: 8px; cursor: pointer; font: inherit; }}
  .addrow:hover {{ background: #232c45; }}
  .help {{ color: #7a85a6; font-size: 12.5px; margin-top: 5px; }}
  details.doc {{ margin-top: 6px; }}
  details.doc summary {{ color: #6f7da6; font-size: 12px; cursor: pointer; user-select: none; list-style: none; width: fit-content; }}
  details.doc summary:hover {{ color: #8ec5ff; }}
  details.doc[open] summary {{ color: #8ec5ff; margin-bottom: 4px; }}
  details.doc > div {{ color: #aab3d0; font-size: 12.5px; line-height: 1.55; background: #11192b; border-left: 2px solid #2c3550; padding: 8px 11px; border-radius: 0 6px 6px 0; }}
  .rowerr input, .rowerr select {{ border-color: #ef4444; }}
  .fielderr {{ color: #f87171; font-size: 12.5px; margin-top: 5px; }}
  .banner {{ padding: 12px 16px; border-radius: 10px; margin: 0 0 18px; font-weight: 600; }}
  .banner.ok {{ background: #10341f; color: #7ee2a8; border: 1px solid #1c5a37; }}
  .banner.err {{ background: #3a1717; color: #f8a3a3; border: 1px solid #6b2626; }}
  .actions {{ position: sticky; bottom: 0; background: #0f1320; padding: 16px 0; border-top: 1px solid #232a40; }}
  button {{ background: #3b82f6; color: #fff; border: 0; padding: 11px 22px; border-radius: 9px; font: inherit; font-weight: 700; cursor: pointer; }}
  button:hover {{ background: #2f6fe0; }}
  .note {{ color: #7a85a6; font-size: 12.5px; }}
  .tabs {{ display: flex; flex-wrap: wrap; gap: 6px; position: sticky; top: 62px; background: #0f1320; padding: 12px 0; margin: 0 0 8px; z-index: 4; }}
  .tab {{ background: #161c2c; color: #9aa4c0; border: 1px solid #232a40; padding: 8px 13px; border-radius: 8px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; }}
  .tab:hover {{ color: #e6e8ef; background: #1b2236; }}
  .tab.active {{ background: #3b82f6; border-color: #3b82f6; color: #fff; }}
  .tabdot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: #ef4444; margin-left: 7px; vertical-align: middle; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
</style></head>
<body>
<header><h1>⚙️ Freelancer Bot — Settings</h1>{_nav("settings")}</header>
<div class="wrap">
  {banner}
  {tabbar}
  <form method="post" action="/save">
    {''.join(sections)}
    <div class="actions"><button type="submit">Save settings</button>
      <span class="note">&nbsp; Dry-run keeps you safe — leave it On until you've verified results.</span>
    </div>
  </form>
</div>
<script>{_PAGE_JS}</script>
<script>{_TABS_JS}</script>
</body></html>"""


_STATUS_COLORS = {
    "new": ("#1e3a5f", "#8ec5ff"),
    "bid": ("#10341f", "#7ee2a8"),
    "filtered": ("#3a2a17", "#f2c08a"),
    "skipped": ("#2a2f40", "#9aa4c0"),
    "error": ("#3a1717", "#f8a3a3"),
}

_JOBS_STYLE = """
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 15px/1.5 system-ui, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f1320; color: #e6e8ef; }
  header { padding: 20px 24px; border-bottom: 1px solid #232a40; position: sticky; top: 0; background: #0f1320; z-index: 5; display:flex; align-items:center; justify-content:space-between; }
  header h1 { font-size: 18px; margin: 0; }
  nav a { color: #9aa4c0; text-decoration: none; font-size: 14px; font-weight: 600; margin-left: 18px; }
  nav a.active, nav a:hover { color: #e6e8ef; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 24px; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
  .filters a { text-decoration: none; padding: 6px 13px; border-radius: 999px; border: 1px solid #2c3550; color: #cdd5ee; font-size: 13px; font-weight: 600; }
  .filters a.on { background: #3b82f6; border-color: #3b82f6; color: #fff; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em; color: #9aa4c0; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #232a40; }
  td { padding: 10px; border-bottom: 1px solid #1d2438; vertical-align: top; }
  tr:hover td { background: #161c2c; }
  .title a { color: #e6e8ef; text-decoration: none; font-weight: 600; }
  .title a:hover { color: #8ec5ff; }
  .meta { color: #7a85a6; font-size: 12px; margin-top: 3px; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11.5px; font-weight: 700; }
  .empty { color: #7a85a6; padding: 40px 0; text-align: center; }
  .note { color: #7a85a6; font-size: 12.5px; }
  .actions { white-space: nowrap; text-align: right; }
  .btn { font: inherit; font-size: 12.5px; font-weight: 600; border-radius: 7px; padding: 6px 11px; cursor: pointer; border: 1px solid #2c3550; background: #1d2438; color: #cdd5ee; }
  .btn:hover { background: #232c45; }
  .btn:disabled { opacity: .5; cursor: default; }
  .btn.apply { border-color: #2c4a7a; color: #9cc3ff; }
  .btn.auto { background: #3b82f6; border-color: #3b82f6; color: #fff; margin-left: 6px; }
  .btn.auto:hover { background: #2f6fe0; }
  .btn.primary { background: #3b82f6; border-color: #3b82f6; color: #fff; }
  .btn.primary:hover { background: #2f6fe0; }
  .done { color: #7ee2a8; font-size: 12px; font-weight: 600; }
  .overlay { position: fixed; inset: 0; background: rgba(6,9,18,.72); display: none; align-items: center; justify-content: center; z-index: 20; padding: 20px; }
  .overlay.show { display: flex; }
  .modal { background: #161c2c; border: 1px solid #2c3550; border-radius: 14px; padding: 20px; width: 100%; max-width: 680px; }
  .modal h3 { margin: 0 0 4px; font-size: 16px; }
  .modal .sub { color: #cdd5ee; font-size: 13.5px; margin-bottom: 8px; font-weight: 600; }
  .jobmeta { color: #8ea0c8; font-size: 12px; margin-bottom: 8px; display: flex; gap: 14px; flex-wrap: wrap; }
  .jobmeta a { color: #8ec5ff; text-decoration: none; }
  .plabel { display: block; font-weight: 600; font-size: 12px; color: #9aa4c0; margin-bottom: 6px; text-transform: uppercase; letter-spacing: .03em; }
  .bidmeta { color: #7ee2a8; font-size: 12px; margin-bottom: 6px; }
  .pnote { color: #f2c08a; font-size: 12px; margin-top: 6px; }
  .modal textarea { width: 100%; min-height: 210px; resize: vertical; padding: 10px 12px; border-radius: 9px; border: 1px solid #2c3550; background: #0f1320; color: #e6e8ef; font: inherit; }
  .modal textarea[readonly] { opacity: .85; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
  .btn.gen { margin-right: auto; border-color: #2c4a7a; color: #9cc3ff; }
  .result { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); max-width: 760px; padding: 12px 18px; border-radius: 10px; font-weight: 600; z-index: 30; box-shadow: 0 8px 30px rgba(0,0,0,.4); }
  .result.ok { background: #10341f; color: #7ee2a8; border: 1px solid #1c5a37; }
  .result.err { background: #3a1717; color: #f8a3a3; border: 1px solid #6b2626; }
"""

# One delegated handler drives every Apply/Auto-bid button and the proposal modal.
# Apply opens a textarea modal then POSTs the typed proposal; Auto-bid confirms,
# then POSTs to let the server generate the proposal with OpenAI and bid. Both hit
# the same JSON endpoints and surface the server's message in a bottom toast.
_JOBS_JS = """
(function () {
  var overlay = document.getElementById('overlay');
  var mText = document.getElementById('m_text');
  var mTitle = document.getElementById('m_title');
  var mSub = document.getElementById('m_sub');
  var mMeta = document.getElementById('m_meta');
  var mLabel = document.getElementById('m_plabel');
  var mBidMeta = document.getElementById('m_bidmeta');
  var mNote = document.getElementById('m_note');
  var mGenerate = document.getElementById('m_generate');
  var mSubmit = document.getElementById('m_submit');
  var mCancel = document.getElementById('m_cancel');
  var currentId = null;

  function toast(msg, ok) {
    var el = document.getElementById('result');
    el.textContent = msg;
    el.className = 'result ' + (ok ? 'ok' : 'err');
    el.hidden = false;
  }
  function closeModal() { overlay.classList.remove('show'); currentId = null; }

  function post(url, id, proposal, btn) {
    var body = 'id=' + encodeURIComponent(id);
    if (proposal != null) body += '&proposal=' + encodeURIComponent(proposal);
    if (btn) { btn.disabled = true; }
    toast('Submitting…', true);
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        toast(d.message || (d.ok ? 'Done.' : 'Failed.'), !!d.ok);
        if (d.ok) { setTimeout(function () { location.reload(); }, 1400); }
        else if (btn) { btn.disabled = false; }
      })
      .catch(function (e) { toast('Request failed: ' + e, false); if (btn) btn.disabled = false; });
  }

  // Open the modal for a job: fetch its detail, then show the description plus
  // either the proposal already sent (read-only) or a settings-generated draft.
  function openDetail(id, title) {
    currentId = id;
    mTitle.textContent = title || ('Project #' + id);
    mSub.textContent = '';
    mMeta.innerHTML = '';
    mBidMeta.textContent = '';
    mNote.textContent = 'Loading…';
    mText.value = '';
    mText.readOnly = true;
    mLabel.textContent = 'Proposal';
    mGenerate.style.display = 'none';
    mSubmit.style.display = 'none';
    mSubmit.disabled = true;
    mCancel.textContent = 'Cancel';
    overlay.classList.add('show');

    fetch('/jobs/detail?id=' + encodeURIComponent(id))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { mNote.textContent = d.message || 'Could not load job.'; return; }
        mSub.textContent = d.title;
        var bits = [];
        if (d.budget) bits.push('<span>💰 ' + d.budget + '</span>');
        if (d.status) bits.push('<span>' + d.status + '</span>');
        if (d.skills) bits.push('<span>' + d.skills + '</span>');
        if (d.url) bits.push('<a href="' + d.url + '" target="_blank" rel="noopener">open on Freelancer ↗</a>');
        mMeta.innerHTML = bits.join('');
        mNote.textContent = d.note || '';
        if (d.mode === 'sent') {
          mLabel.textContent = 'Proposal you sent';
          mText.value = d.proposal || '';
          mText.readOnly = true;
          if (d.bid_info) mBidMeta.textContent = '✓ ' + d.bid_info;
          mGenerate.style.display = 'none';
          mSubmit.style.display = 'none';
          mCancel.textContent = 'Close';
        } else {
          mLabel.textContent = 'Proposal — write it yourself, or click Generate with AI';
          mText.value = d.proposal || '';
          mText.readOnly = false;
          mGenerate.style.display = '';
          mGenerate.disabled = false;
          mSubmit.style.display = '';
          mSubmit.disabled = false;
          mText.focus();
        }
      })
      .catch(function (e) { mNote.textContent = 'Request failed: ' + e; });
  }

  document.querySelectorAll('button.apply, button.view').forEach(function (b) {
    b.addEventListener('click', function () {
      openDetail(b.getAttribute('data-id'), b.getAttribute('data-title'));
    });
  });
  document.querySelectorAll('button.auto').forEach(function (b) {
    b.addEventListener('click', function () {
      var t = b.getAttribute('data-title') || ('#' + b.getAttribute('data-id'));
      if (!confirm('Generate a proposal with OpenAI and submit a bid to:\\n\\n' + t + ' ?')) return;
      post('/jobs/auto-bid', b.getAttribute('data-id'), null, b);
    });
  });

  // On-demand generation: only spends an OpenAI call when the user clicks it.
  mGenerate.addEventListener('click', function () {
    if (!currentId) return;
    mGenerate.disabled = true;
    mNote.textContent = 'Generating with AI…';
    fetch('/jobs/generate?id=' + encodeURIComponent(currentId))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) { mText.value = d.proposal || ''; mNote.textContent = ''; mText.focus(); }
        else { mNote.textContent = d.message || 'Generation failed.'; }
        mGenerate.disabled = false;
      })
      .catch(function (e) { mNote.textContent = 'Generation failed: ' + e; mGenerate.disabled = false; });
  });

  mCancel.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
  mSubmit.addEventListener('click', function () {
    var txt = mText.value.trim();
    if (!txt) { mText.focus(); return; }
    var id = currentId;
    closeModal();
    post('/jobs/apply', id, txt, null);
  });
})();
"""


def _nav(active: str) -> str:
    def link(href: str, label: str, key: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a href="{href}"{cls}>{label}</a>'
    return (
        "<nav>"
        + link("/", "Settings", "settings")
        + link("/jobs", "Jobs", "jobs")
        + "</nav>"
    )


FREELANCER_WEB_BASE = "https://www.freelancer.com"


def _job_url(raw: Any) -> str:
    """Absolute project URL. The API stores ``seo_url`` as a bare category/slug
    path (e.g. ``ui-design/app-application``). The public project page lives at
    ``/projects/<seo_url>/details``, so we add the ``projects/`` prefix and the
    ``/details`` suffix (idempotently) on top of the Freelancer base."""
    u = ("" if raw is None else str(raw)).strip()
    if not u:
        return ""
    if u.startswith(("http://", "https://")):
        return u
    u = u.strip("/")
    if not u.startswith("projects/"):
        u = "projects/" + u
    if not u.endswith("/details"):
        u = u + "/details"
    return f"{FREELANCER_WEB_BASE}/{u}"


def _fmt_budget(row: Any) -> str:
    cur = (row["currency"] or "").strip()
    lo, hi = row["budget_min"], row["budget_max"]

    def n(x: Any) -> str:
        if x is None:
            return ""
        return str(int(x)) if float(x).is_integer() else f"{float(x):g}"

    if lo is None and hi is None:
        return "—"
    if lo is not None and hi is not None and lo != hi:
        amount = f"{n(lo)}–{n(hi)}"
    else:
        amount = n(lo if lo is not None else hi)
    return f"{amount} {cur}".strip()


def _render_jobs(rows: list, counts: list, active_status: str | None) -> str:
    def esc(x: Any) -> str:
        return html.escape("" if x is None else str(x), quote=True)

    total = sum(int(c["c"]) for c in counts)
    chips = [
        f'<a href="/jobs" class="{"on" if not active_status else ""}">All ({total})</a>'
    ]
    for c in counts:
        st = c["status"]
        sel = "on" if active_status == st else ""
        q = urlencode({"status": st})
        chips.append(f'<a href="/jobs?{q}" class="{sel}">{esc(st)} ({int(c["c"])})</a>')

    if rows:
        body = []
        for r in rows:
            st = (r["status"] or "new")
            bg, fg = _STATUS_COLORS.get(st, ("#2a2f40", "#cdd5ee"))
            url = _job_url(r["url"])
            title = esc(r["title"] or f"Project {r['id']}")
            title_html = (
                f'<a href="{esc(url)}" target="_blank" rel="noopener">{title}</a>'
                if url else title
            )
            skills = esc(r["skills"] or "")
            reason = r["filter_reason"]
            meta_bits = [b for b in (f"#{r['id']}", skills) if b]
            if st == "filtered" and reason:
                meta_bits.append(f"reason: {esc(reason)}")
            score = r["score"] if r["score"] is not None else 0
            bids = r["bid_count"] if r["bid_count"] is not None else "—"
            # A project we've already bid on (real or draft) shows a marker instead
            # of buttons so it can't be double-submitted. The bids table enforces
            # one row per project, so a second attempt would fail anyway.
            acted = st.startswith("bid") or "sent" in st or "dry_run" in st
            if acted:
                actions = f'<button class="btn view" data-id="{esc(r["id"])}" data-title="{title}">✓ View</button>'
            else:
                actions = (
                    f'<button class="btn apply" data-id="{esc(r["id"])}" data-title="{title}">Apply</button>'
                    f'<button class="btn auto" data-id="{esc(r["id"])}" data-title="{title}">Auto-bid</button>'
                )
            body.append(
                "<tr>"
                f'<td class="title">{title_html}<div class="meta">{" · ".join(meta_bits)}</div></td>'
                f'<td><span class="pill" style="background:{bg};color:{fg}">{esc(st)}</span></td>'
                f'<td class="num">{esc(score)}</td>'
                f'<td class="num">{esc(_fmt_budget(r))}</td>'
                f'<td class="num">{esc(bids)}</td>'
                f'<td class="num">{esc((r["created_at"] or "")[:16])}</td>'
                f'<td class="actions">{actions}</td>'
                "</tr>"
            )
        table = (
            "<table><thead><tr>"
            "<th>Project</th><th>Status</th><th>Score</th><th>Budget</th><th>Bids</th><th>Created (UTC)</th><th></th>"
            "</tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table>"
        )
    else:
        table = '<div class="empty">No jobs stored yet. They appear here once the bot has run a polling cycle.</div>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Freelancer Bot — Jobs</title>
<style>{_JOBS_STYLE}</style></head>
<body>
<header><h1>📋 Freelancer Bot — Jobs</h1>{_nav("jobs")}</header>
<div class="wrap">
  <div class="filters">{''.join(chips)}</div>
  {table}
  <p class="note">Showing up to 500 most recent projects. Times are UTC. <b>Apply</b> submits a proposal you write; <b>Auto-bid</b> generates one with OpenAI and submits it. While <b>Dry run</b> is ON (Settings) both only save a draft — no real bid is placed.</p>
</div>
<div id="overlay" class="overlay"><div class="modal">
  <h3 id="m_title">Apply</h3>
  <div class="sub" id="m_sub"></div>
  <div class="jobmeta" id="m_meta"></div>
  <label class="plabel" id="m_plabel" for="m_text">Proposal</label>
  <div class="bidmeta" id="m_bidmeta"></div>
  <textarea id="m_text" placeholder="Write your proposal here…"></textarea>
  <div class="pnote" id="m_note"></div>
  <div class="modal-actions">
    <button class="btn gen" id="m_generate" type="button">✨ Generate with AI</button>
    <button class="btn" id="m_cancel" type="button">Cancel</button>
    <button class="btn primary" id="m_submit" type="button">Submit bid</button>
  </div>
</div></div>
<div id="result" class="result" hidden></div>
<script>{_JOBS_JS}</script>
</body></html>"""


def _validate(submitted: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (updates_to_write, errors). Secrets with empty input are dropped from
    updates so the stored value is preserved; ints are range/format checked."""
    updates: dict[str, str] = {}
    errors: dict[str, str] = {}
    for key, field in ALL_FIELDS.items():
        raw = submitted.get(key, "")
        val = raw.strip()
        if field.kind == "secret":
            if val == "":
                continue  # keep existing secret
            updates[key] = val
        elif field.kind == "bool":
            updates[key] = "1" if _is_true(val) else "0"
        elif field.kind == "int":
            if val == "":
                updates[key] = ""
                continue
            try:
                n = int(val)
            except ValueError:
                errors[key] = "Must be a whole number."
                continue
            if n < 0:
                errors[key] = "Must be 0 or greater."
                continue
            updates[key] = str(n)
        elif field.kind == "time":
            if val == "":
                updates[key] = ""
            elif _valid_hhmm(val):
                updates[key] = val
            else:
                errors[key] = "Use HH:MM (24-hour)."
        elif field.kind == "textarea":
            if val == "":
                updates[key] = ""
            else:
                # Escape + double-quote so newlines survive the line-based .env.
                esc = raw.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
                updates[key] = f'"{esc}"'
        elif field.kind == "bidrules":
            ok, normalized, err = _normalize_bid_rules(raw)
            if not ok:
                errors[key] = err
            else:
                updates[key] = normalized
        elif field.kind == "telegrambots":
            ok, normalized, err = _normalize_telegram_bots(raw)
            if not ok:
                errors[key] = err
            else:
                updates[key] = normalized
        else:  # csv | text
            updates[key] = val
    return updates, errors


def _valid_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


def _normalize_bid_rules(raw: str) -> tuple[bool, str, str]:
    """Validate the JSON bid-rules payload and return a compact re-serialised form.

    Returns (ok, compact_json, error_message). Empty input is valid and clears the
    env var (returns "")."""
    if not raw or not raw.strip():
        return True, "", ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False, "", "Bid rules are malformed (could not parse)."
    if not isinstance(data, list):
        return False, "", "Bid rules must be a list."
    out: list[dict] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        currencies = [str(c).strip().upper() for c in (r.get("currencies") or []) if str(c).strip()]
        try:
            rule = {
                "currencies": currencies,
                "min": float(r.get("min", 0) or 0),
                "max": float(r.get("max", 0) or 0),
                "bid": float(r.get("bid", 0) or 0),
                "delivery": int(float(r.get("delivery", 0) or 0)),
            }
        except (TypeError, ValueError):
            return False, "", "Bid rules contain a non-numeric value."
        if rule["max"] < rule["min"]:
            return False, "", "A bid rule has Max smaller than Min."
        out.append(rule)
    return True, json.dumps(out, separators=(",", ":")), ""


def _normalize_telegram_bots(raw: str) -> tuple[bool, str, str]:
    """Validate the extra-bots JSON: a list of {token, chat_id}, both required.

    Returns (ok, compact_json, error_message). Empty input clears the var."""
    if not raw or not raw.strip():
        return True, "", ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False, "", "Extra bots are malformed (could not parse)."
    if not isinstance(data, list):
        return False, "", "Extra bots must be a list."
    out: list[dict] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        token = str(r.get("token", "")).strip()
        chat_id = str(r.get("chat_id", "")).strip()
        if not token and not chat_id:
            continue
        if not token or not chat_id:
            return False, "", "Each extra bot needs BOTH a token and a chat id."
        out.append({"token": token, "chat_id": chat_id})
    return True, json.dumps(out, separators=(",", ":")), ""


def _resolve_pricing(s, proj: dict) -> tuple[float, int]:
    """Bid amount + delivery days for a project, mirroring the webhook precedence:
    AI auto-pricing (if on) -> structured bid rules -> budget heuristic."""
    from .proposal_ai import ai_price_and_duration
    from .webhook import _choose_bid_amount, _choose_period_days, choose_bid_from_rules

    if s.ai_pricing_enabled and s.openai_api_key and s.ai_pricing_rules.strip():
        priced = ai_price_and_duration(
            s.openai_api_key, s.openai_model, s.ai_pricing_rules,
            title=proj["title"] or "", description=proj["description"] or "",
            skills=proj["skills"] or "", budget_min=proj["budget_min"],
            budget_max=proj["budget_max"], currency=proj["currency"],
        )
        if priced is not None:
            return priced
    ruled = choose_bid_from_rules(proj["currency"], proj["budget_min"], proj["budget_max"], s.bid_rules)
    if ruled is not None:
        return ruled
    return (
        _choose_bid_amount(proj["budget_min"], proj["budget_max"]),
        _choose_period_days(proj["budget_min"], proj["budget_max"]),
    )


def _generate_proposal(s, proj: dict) -> tuple[str | None, str | None]:
    """Generate a proposal for ``proj`` following the user's settings (identity,
    toggles, template, prefix/suffix). Returns ``(text, error)`` — exactly one is
    non-None."""
    if not s.openai_api_key:
        return None, "No OpenAI API key set — write the proposal manually or set OPENAI_API_KEY in Settings."
    from .proposal_ai import build_proposal_input, generate_proposal_openai
    try:
        text = generate_proposal_openai(
            api_key=s.openai_api_key, model=s.openai_model,
            data=build_proposal_input(
                s, title=proj["title"] or "", description=proj["description"] or "",
                skills=proj["skills"] or "", budget_min=proj["budget_min"],
                budget_max=proj["budget_max"], currency=proj["currency"],
                questions=["What is your ideal deadline?"],
            ),
        )
        return text, None
    except Exception as exc:
        return None, f"OpenAI generation failed: {type(exc).__name__}: {str(exc)[:200]}"


def _action_bid(project_id: int, proposal_text: str | None, auto: bool) -> tuple[bool, str]:
    """Place (or, under dry-run, draft) a bid on a stored project.

    ``auto`` generates the proposal with OpenAI; otherwise ``proposal_text`` is used
    verbatim. Returns ``(ok, message)`` for the JSON response. Honours the bot's
    safety controls: BOT_DRY_RUN saves a draft instead of bidding, the daily cap is
    enforced, and the one-bid-per-project rule blocks duplicates."""
    from .config import load_settings
    from .db import (
        bid_exists_for_project, connect, count_bids_today, get_project,
        init_db, insert_bid, set_project_score_and_status,
    )

    try:
        s = load_settings()
    except Exception as exc:  # missing FLN token, etc.
        return False, f"Config error: {exc}"

    conn = connect()
    try:
        init_db(conn)
        row = get_project(conn, project_id)
        if row is None:
            return False, f"Project {project_id} is not in the local DB."
        proj = {k: row[k] for k in row.keys()}

        if bid_exists_for_project(conn, int(project_id)):
            return False, "A bid (or draft) already exists for this project — not submitting again."

        if auto:
            proposal, err = _generate_proposal(s, proj)
            if err:
                return False, err
        else:
            proposal = (proposal_text or "").strip()
            if not proposal:
                return False, "Proposal text is empty."

        amount, period = _resolve_pricing(s, proj)
        milestone = s.default_milestone_percent
        cur = proj["currency"] or ""
        score = int(proj["score"] or 0)

        if s.dry_run:
            insert_bid(conn, int(project_id), None, float(amount), int(period), milestone, proposal, status="webui_dry_run")
            set_project_score_and_status(conn, int(project_id), score, "bid_draft")
            return True, (
                f"Dry run is ON — saved a draft proposal ({amount:g} {cur}, {int(period)}d). "
                "No real bid was placed. Turn Dry run OFF in Settings to submit for real."
            )

        if s.max_bids_per_day and count_bids_today(conn) >= s.max_bids_per_day:
            return False, f"Daily bid cap reached ({s.max_bids_per_day}). Raise BOT_MAX_BIDS_PER_DAY in Settings or wait."

        from .bidder import BidRequest, place_bid
        from .freelancer_client import make_session
        from .webhook import _get_my_user_id

        session = make_session(s.fln_oauth_token, s.fln_url)
        bidder_id = _get_my_user_id(session)
        resp = place_bid(session, BidRequest(
            project_id=int(project_id), bidder_id=bidder_id, description=proposal,
            amount=float(amount), period_days=int(period), milestone_percent=milestone,
        ))
        bid_id = None
        if isinstance(resp, dict):
            res = resp.get("result") or {}
            bid_id = res.get("id") or res.get("bid_id")
        insert_bid(conn, int(project_id), int(bid_id) if bid_id else None, float(amount), int(period), milestone, proposal, status="webui_sent")
        set_project_score_and_status(conn, int(project_id), score, "bid")
        label = "Auto-bid" if auto else "Bid"
        tail = f", bid id {bid_id}" if bid_id else ""
        return True, f"{label} placed on #{project_id} ({amount:g} {cur}, {int(period)}d){tail}."
    except Exception as exc:
        return False, f"Failed: {type(exc).__name__}: {str(exc)[:200]}"
    finally:
        conn.close()


def _job_detail(project_id: int) -> tuple[int, dict]:
    """Detail for the Apply/View modal. For an already-bid project it returns the
    proposal that was sent (mode='sent', read-only). For a fresh project it returns
    an EMPTY draft (mode='draft') — no OpenAI call. Generation is on-demand via
    :func:`_generate_for_job` (the 'Generate with AI' button), so opening Apply and
    closing without submitting never spends an API credit."""
    from .db import connect, get_latest_bid, get_project, init_db

    conn = connect()
    try:
        init_db(conn)
        row = get_project(conn, project_id)
        if row is None:
            return 404, {"ok": False, "message": f"Project {project_id} not found."}
        proj = {k: row[k] for k in row.keys()}
        bid = get_latest_bid(conn, project_id)
    finally:
        conn.close()

    detail = {
        "ok": True,
        "id": proj["id"],
        "title": proj["title"] or f"Project {proj['id']}",
        "description": proj["description"] or "(no description stored)",
        "skills": proj["skills"] or "",
        "budget": _fmt_budget(proj),
        "url": _job_url(proj["url"]),
        "status": proj["status"] or "new",
    }

    if bid is not None:
        amt = bid["amount"]
        detail["mode"] = "sent"
        detail["proposal"] = bid["proposal"] or ""
        detail["bid_info"] = " · ".join([
            str(bid["status"] or "bid"),
            (f"{amt:g} {proj['currency'] or ''}".strip() if amt is not None else "—"),
            f"{bid['period_days']}d" if bid["period_days"] is not None else "",
            f"{(bid['created_at'] or '')[:16]} UTC" if bid["created_at"] else "",
        ]).strip(" ·")
        return 200, detail

    detail["mode"] = "draft"
    detail["proposal"] = ""
    return 200, detail


def _generate_for_job(project_id: int) -> tuple[int, dict]:
    """On-demand proposal generation for the Apply modal's 'Generate with AI'
    button. Returns ``(code, {ok, proposal})`` or an error payload. This is the
    ONLY place the Apply flow spends an OpenAI call."""
    from .config import load_settings
    from .db import connect, get_project, init_db

    try:
        s = load_settings()
    except Exception as exc:
        return 400, {"ok": False, "message": f"Config error: {exc}"}
    conn = connect()
    try:
        init_db(conn)
        row = get_project(conn, project_id)
        if row is None:
            return 404, {"ok": False, "message": f"Project {project_id} not found."}
        proj = {k: row[k] for k in row.keys()}
    finally:
        conn.close()
    text, err = _generate_proposal(s, proj)
    if err:
        return 400, {"ok": False, "message": err}
    return 200, {"ok": True, "proposal": text}


def serve_webui(host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _send_html(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            return {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            if path in ("/jobs/detail", "/jobs/generate"):
                try:
                    pid = int((query.get("id", [""])[0] or "").strip())
                except (TypeError, ValueError):
                    self._send_json(400, {"ok": False, "message": "Bad or missing project id."})
                    return
                code, payload = (_generate_for_job(pid) if path == "/jobs/generate" else _job_detail(pid))
                self._send_json(code, payload)
                return
            if path == "/jobs":
                status = (query.get("status", [""])[0] or "").strip() or None
                conn = db.connect()
                try:
                    db.init_db(conn)
                    rows = db.list_all_projects(conn, status=status)
                    counts = db.count_projects_by_status(conn)
                finally:
                    conn.close()
                self._send_html(200, _render_jobs(rows, counts, status))
                return
            if path not in ("/", "/index.html"):
                self.send_error(404)
                return
            saved = "saved=1" in self.path
            self._send_html(200, _render(read_env(), saved=saved, errors={}))

        def do_POST(self) -> None:  # noqa: N802
            if self.path in ("/jobs/apply", "/jobs/auto-bid"):
                form = self._read_form()
                try:
                    pid = int(form.get("id", "").strip())
                except (TypeError, ValueError):
                    self._send_json(400, {"ok": False, "message": "Bad or missing project id."})
                    return
                auto = self.path == "/jobs/auto-bid"
                ok, message = _action_bid(pid, form.get("proposal"), auto)
                self._send_json(200 if ok else 400, {"ok": ok, "message": message})
                return
            if self.path != "/save":
                self.send_error(404)
                return
            form = self._read_form()
            updates, errors = _validate(form)
            if errors:
                # Re-render with what they typed (minus secrets) so nothing is lost.
                current = read_env()
                current.update({k: v for k, v in form.items() if ALL_FIELDS.get(k) and ALL_FIELDS[k].kind != "secret"})
                self._send_html(400, _render(current, saved=False, errors=errors))
                return
            update_env(updates)
            self.send_response(303)
            self.send_header("Location", "/?saved=1")
            self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            print(f"[webui] {self.address_string()} - {fmt % args}")

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Settings UI on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb UI stopped.")
