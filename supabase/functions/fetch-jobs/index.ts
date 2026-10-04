// Supabase Edge Function: the job fetcher, replacing the Python `run-loop` worker.
//
// pg_cron calls it every 30 seconds (supabase/fetcher_cron.sql). Each call:
//   1. claims the cycle in worker_status (skips when another run holds it, or when
//      "Fetch every" in Admin › Bot settings hasn't passed yet; "Fetch now" overrides),
//   2. searches Freelancer for new projects (newest first, stops at the first page
//      with nothing new), filters and scores them like bot/filters.py + bot/scorer.py,
//   3. stores each one in `jobs` (the Jobs page shows it at once via Realtime),
//   4. sends the Telegram / Slack alert, then records the outcome in jobs.status,
//   5. writes the heartbeat the Admin page shows.
// Settings come from app_settings, with the same defaults as bot/config.py.
//
// Ports of: bot/collector.py, bot/filters.py, bot/scorer.py, bot/telegram_notify.py,
// bot/slack_notify.py, ai_filter_project in bot/proposal_ai.py. Change both together.
//
// Deploy: supabase functions deploy fetch-jobs --no-verify-jwt
// Secret: supabase secrets set FETCH_JOBS_SECRET=<random string> (also stored in Vault
// for the cron job, see supabase/fetcher_cron.sql).

import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2";

type Values = Record<string, string>;
type Json = Record<string, unknown>;
type Client = {
  available: boolean;
  reason?: string;
  country?: unknown;
  country_code?: string | null;
  country_name?: string | null;
  completed_jobs?: number | null;
  payment_verified?: boolean | null;
};
type Row = {
  id: number;
  title: string;
  url: string | null;
  description: string;
  currency: string | null;
  budget_min: number | null;
  budget_max: number | null;
  bid_count: number | null;
  bid_avg: number | null;
  skills: string;
  created_at: number | null; // epoch seconds
  bidperiod: number | null;
  owner_id: number | null;
  upgrades: Json | null;
  type: string | null;
};

const PAGE_SIZE = 50;
const MAX_PAGES = 20;
// Stop paging after this long so a cold start can't hit the function's time limit.
const MAX_FETCH_MS = 60_000;
// The cycle lock; longer than any run (the function is killed at 150 s anyway).
const LEASE_SECONDS = 170;
// Freelancer user lookups / AI checks running at once.
const CONCURRENCY = 4;
// Excluded projects are remembered this long so they aren't re-evaluated.
const SEEN_DAYS = 3;

// Defaults of bot/config.py (except the 30 s interval), for keys the admin never saved.
const DEFAULTS: Values = {
  BOT_FETCH_ENABLED: "1",
  BOT_NOTIFY_ENABLED: "1",
  BOT_POLL_INTERVAL_SECONDS: "30",
  BOT_MIN_SCORE: "70",
  BOT_MIN_BUDGET_USD: "200",
  BOT_MIN_SKILL_MATCHES: "2",
  BOT_MIN_CLIENT_COMPLETED_JOBS: "1",
  BOT_MAX_PROJECT_AGE_SECONDS: "3600",
  BOT_MIN_BID_REMAINING_SECONDS: "0",
  OPENAI_MODEL: "gpt-5.2-mini",
};

// [env suffix, upgrades key] — SKIPPABLE_UPGRADES in bot/filters.py.
const SKIPPABLE_UPGRADES: [string, string][] = [
  ["NDA", "NDA"], ["PREFERRED", "pf_only"], ["SEALED", "sealed"], ["QUALIFIED", "qualified"],
  ["IP_CONTRACT", "ip_contract"], ["NON_COMPETE", "non_compete"], ["NONPUBLIC", "nonpublic"],
  ["FULLTIME", "fulltime"], ["RECRUITER", "recruiter"],
];
// [upgrades key, label] — DISPLAY_UPGRADES in bot/filters.py.
const DISPLAY_UPGRADES: [string, string][] = [
  ["NDA", "NDA"], ["pf_only", "Preferred"], ["sealed", "Sealed"], ["qualified", "Verified"],
  ["ip_contract", "IP"], ["non_compete", "Non-compete"], ["nonpublic", "Private"],
  ["urgent", "Urgent"], ["featured", "Featured"], ["fulltime", "Full-time"], ["recruiter", "Recruiter"],
];

// ── Settings ─────────────────────────────────────────────────────────────────────
const TRUE = new Set(["1", "true", "yes", "y", "on"]);
const csv = (v: string | undefined) => (v ?? "").split(",").map((x) => x.trim()).filter(Boolean);

class Settings {
  constructor(private v: Values) {}
  str(k: string) { return (this.v[k] ?? "").trim(); }
  text(k: string) { return (this.v[k] ?? "").replace(/\\n/g, "\n").trim(); }
  bool(k: string, d: boolean) { return k in this.v ? TRUE.has(this.v[k].trim().toLowerCase()) : d; }
  int(k: string) {
    const raw = (this.v[k] ?? "").split("#")[0].trim();
    const n = /^-?\d+$/.test(raw) ? parseInt(raw, 10) : NaN;
    return Number.isNaN(n) ? parseInt(DEFAULTS[k] ?? "0", 10) : n;
  }
  float(k: string): number | null {
    const raw = (this.v[k] ?? "").split("#")[0].trim().replace(/^\+/, "");
    const n = raw === "" ? NaN : Number(raw);
    return Number.isNaN(n) ? null : n;
  }
  list(k: string) { return csv(this.v[k]); }
  skipCurrencies() { const l = this.list("BOT_SKIP_CURRENCIES"); return l.length ? l : ["INR"]; }
  skipUpgrades() { return SKIPPABLE_UPGRADES.filter(([s]) => this.bool(`BOT_SKIP_${s}`, false)).map(([, k]) => k); }
  base() { return (this.str("FLN_URL") || "https://www.freelancer.com").replace(/\/+$/, ""); }

  /** Every (token, chat) an alert goes to, deduplicated — _telegram_targets(). */
  telegramTargets(): [string, string][] {
    const out: [string, string][] = [];
    const tok = this.str("TELEGRAM_BOT_TOKEN");
    if (tok) for (const c of this.list("TELEGRAM_CHAT_ID")) out.push([tok, c]);
    try {
      const extra = JSON.parse(this.v.TELEGRAM_BOTS || "[]");
      if (Array.isArray(extra)) {
        for (const b of extra) {
          const t = String(b?.token ?? "").trim();
          const c = String(b?.chat_id ?? "").trim();
          if (t && c) out.push([t, c]);
        }
      }
    } catch { /* malformed: primary bot only */ }
    const seen = new Set<string>();
    return out.filter(([t, c]) => !seen.has(`${t}|${c}`) && seen.add(`${t}|${c}`));
  }
}

// ── Time windows (bot/util.py) ───────────────────────────────────────────────────
function hhmm(v: string): number | null {
  const m = /^(\d{1,2})(?::(\d{1,2}))?$/.exec(v.trim());
  if (!m) return null;
  const h = +m[1], mi = +(m[2] ?? 0);
  return h <= 23 && mi <= 59 ? h * 60 + mi : null;
}

/** Minutes since midnight, in UTC + BOT_ACTIVE_TZ_OFFSET (blank offset = UTC). */
function nowMinutes(offset: number | null): number {
  const d = new Date();
  return (((d.getUTCHours() * 60 + d.getUTCMinutes() + Math.round((offset ?? 0) * 60)) % 1440) + 1440) % 1440;
}

function withinWindow(start: string, end: string, now: number): boolean {
  const a = hhmm(start), b = hhmm(end);
  if (a === null || b === null || a === b) return true;
  return a < b ? a <= now && now < b : now >= a || now < b;
}

const fmtMinutes = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

// ── Freelancer API ───────────────────────────────────────────────────────────────
async function flnGet(s: Settings, path: string, params: Record<string, string | number>): Promise<Json> {
  const url = new URL(`${s.base()}/api/${path}`);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  const r = await fetch(url, { headers: { "freelancer-oauth-v1": s.str("FLN_OAUTH_TOKEN") } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`Freelancer ${path} HTTP ${r.status}: ${String(body?.message ?? "").slice(0, 200)}`);
  return (body?.result ?? {}) as Json;
}

function toRow(p: Json): Row {
  const budget = (p.budget ?? {}) as Json;
  const cur = p.currency as Json | string | undefined;
  const currency = (typeof cur === "object" && cur ? cur.code as string : cur as string) ||
    ((budget.currency as Json | undefined)?.code as string) || null;
  const stats = (p.bid_stats ?? {}) as Json;
  const num = (x: unknown) => (typeof x === "number" ? x : null);
  const created = p.time_submitted ?? p.date_submitted ?? p.time_created;
  return {
    id: Number(p.id),
    title: String(p.title ?? ""),
    url: (p.seo_url as string) || (p.url as string) || null,
    description: String(p.description || p.preview_description || ""),
    currency,
    budget_min: num(budget.minimum),
    budget_max: num(budget.maximum),
    bid_count: num(stats.bid_count),
    bid_avg: num(stats.bid_avg),
    skills: ((p.jobs as Json[] | undefined) ?? []).filter((j) => j && typeof j === "object").map((j) => String(j.name)).join(","),
    created_at: typeof created === "number" ? created : typeof created === "string" && created.trim() && !Number.isNaN(Number(created)) ? Number(created) : null,
    bidperiod: num(p.bidperiod),
    owner_id: num(p.owner_id) ?? num(p.user_id),
    upgrades: p.upgrades && typeof p.upgrades === "object" ? p.upgrades as Json : null,
    type: p.type === "hourly" || p.type === "fixed" ? p.type : null,
  };
}

function completedJobs(user: Json): number | null {
  const keys = ["completed_jobs", "complete_jobs", "jobs_completed", "completed_projects", "projects_completed", "complete", "completed"];
  const er = user.employer_reputation as Json | undefined;
  for (const obj of [er?.entire_history, er, user.reputation]) {
    if (!obj || typeof obj !== "object") continue;
    for (const k of keys) {
      const v = (obj as Json)[k];
      if (typeof v === "number") return Math.trunc(v);
    }
  }
  const walk = (node: unknown, depth: number): number | null => {
    if (depth > 5 || !node || typeof node !== "object") return null;
    if (Array.isArray(node)) {
      for (const item of node) { const got = walk(item, depth + 1); if (got !== null) return got; }
      return null;
    }
    for (const [k, v] of Object.entries(node)) {
      const key = k.toLowerCase();
      if (key.includes("complete") && (key.includes("job") || key.includes("project")) && typeof v === "number") return Math.trunc(v);
      const got = walk(v, depth + 1);
      if (got !== null) return got;
    }
    return null;
  };
  return walk(user, 0);
}

async function clientStatus(s: Settings, ownerId: number | null): Promise<Client> {
  if (ownerId == null) return { available: false, reason: "missing_owner_id" };
  try {
    const raw = await flnGet(s, `users/0.1/users/${ownerId}/`, {
      status: "true", reputation: "true", employer_reputation: "true", display_info: "true", country: "true", compact: "true",
    });
    const user = (raw.user && typeof raw.user === "object" ? raw.user : raw) as Json;
    const country = user.country;
    const c = country && typeof country === "object" ? country as Json : null;
    const code = typeof country === "string" ? (country.trim().length <= 3 ? country.trim().toUpperCase() : null)
      : (["code", "country_code", "iso2", "iso3"].map((k) => c?.[k]).find((v) => typeof v === "string" && v.trim()) as string | undefined)?.trim().toUpperCase() ?? null;
    const name = typeof country === "string" ? country.trim() || null
      : (["name", "country_name"].map((k) => c?.[k]).find((v) => typeof v === "string" && v.trim()) as string | undefined)?.trim() ?? null;
    const status = user.status as Json | undefined;
    return {
      available: true,
      country,
      country_code: code,
      country_name: name,
      completed_jobs: completedJobs(user),
      payment_verified: typeof status?.payment_verified === "boolean" ? status.payment_verified : null,
    };
  } catch (e) {
    return { available: false, reason: `client_lookup_failed:${(e as Error).message}` };
  }
}

// ── Filters (bot/filters.py evaluate_project) ────────────────────────────────────
function cheapFilter(s: Settings, r: Row, now: number): string | null {
  const maxAge = s.int("BOT_MAX_PROJECT_AGE_SECONDS");
  if (maxAge > 0 && r.created_at != null && now - r.created_at > maxAge) return `project_too_old:${Math.trunc(now - r.created_at)}s`;

  const minLeft = s.int("BOT_MIN_BID_REMAINING_SECONDS");
  if (minLeft > 0 && r.created_at != null) {
    const left = r.created_at + (r.bidperiod ?? 7) * 86400 - now;
    if (left < minLeft) return `bid_ending_soon:${Math.trunc(left)}s_left`;
  }

  const code = (r.currency ?? "").trim().toUpperCase();
  if (code && s.skipCurrencies().map((c) => c.toUpperCase()).includes(code)) return `currency_skipped:${code}`;

  if (r.upgrades) for (const key of s.skipUpgrades()) if (r.upgrades[key]) return `upgrade_blocked:${key}`;

  const title = r.title.toLowerCase();
  for (const k of s.list("BOT_EXCLUDE_TITLE_KEYWORDS").map((x) => x.toLowerCase())) if (title.includes(k)) return `title_keyword_blocked:${k}`;
  const desc = r.description.toLowerCase();
  for (const k of s.list("BOT_EXCLUDE_DESC_KEYWORDS").map((x) => x.toLowerCase())) if (desc.includes(k)) return `desc_keyword_blocked:${k}`;

  const badges = r.skills.split(",").map((b) => b.trim().toLowerCase()).filter(Boolean);
  for (const ex of s.list("BOT_EXCLUDE_SKILLS").map((x) => x.toLowerCase())) {
    if (badges.some((b) => b === ex || b.includes(ex))) return `skill_blocked:${ex}`;
  }

  const budget = r.budget_max ?? r.budget_min;
  if (budget != null && budget < s.int("BOT_MIN_BUDGET_USD")) return `budget_too_low:${budget}`;
  return null;
}

function clientFilter(s: Settings, c: Client): string | null {
  const codes = new Set([(c.country_code ?? "").trim().toUpperCase()].filter(Boolean));
  const names = new Set([(c.country_name ?? "").trim().toLowerCase()].filter(Boolean));
  if (typeof c.country === "string") {
    const rc = c.country.trim();
    if (rc.length === 2) codes.add(rc.toUpperCase());
    else if (rc) names.add(rc.toLowerCase());
  }
  // ISO code or name match; entries of 4+ chars also match inside the name
  // ("korea" ~ "South Korea"), short ones only exactly.
  const matches = (configured: string[]) => configured.some((v) => {
    if (codes.has(v.toUpperCase())) return true;
    const vl = v.toLowerCase();
    return [...names].some((n) => vl === n || (vl.length >= 4 && (n.includes(vl) || vl.includes(n))));
  });
  const allow = s.list("BOT_ALLOW_COUNTRIES");
  const skip = s.list("BOT_SKIP_COUNTRIES");
  if (allow.length && !matches(allow)) return "country_not_allowed";
  if (skip.length && matches(skip)) return "country_blocked";

  const minCompleted = Math.max(0, s.int("BOT_MIN_CLIENT_COMPLETED_JOBS"));
  if (minCompleted > 0 && (c.completed_jobs == null || c.completed_jobs < minCompleted)) return "client_completed_jobs_too_low";
  return null;
}

// ── Score (bot/scorer.py) ────────────────────────────────────────────────────────
function score(s: Settings, r: Row): number {
  if ((r.currency ?? "").trim().toUpperCase() === "INR") return 0;
  const text = `${r.title}\n${r.description}`.toLowerCase();
  const skillsText = r.skills.toLowerCase();
  let total = (r.budget_max ?? r.budget_min) == null ? 5 : 25;
  const matches = s.list("BOT_SKILLS").filter((k) => text.includes(k.toLowerCase()) || skillsText.includes(k.toLowerCase())).length;
  if (matches < Math.max(1, s.int("BOT_MIN_SKILL_MATCHES"))) return 0;
  total += Math.min(50, matches * 15);
  if (matches >= 2) total += 10;
  if (matches >= 4) total += 10;
  if (r.description.length > 600) total += 10;
  else if (r.description.length > 200) total += 5;
  return Math.max(0, Math.min(100, total));
}

// ── AI filter (ai_filter_project) — fails open ──────────────────────────────────
function extractJson(text: string): Json {
  try { return JSON.parse(text); } catch { /* fall through */ }
  const m = /\{[\s\S]*\}/.exec(text);
  if (m) { try { const o = JSON.parse(m[0]); return o && typeof o === "object" ? o : {}; } catch { /* none */ } }
  return {};
}

async function aiFilter(s: Settings, r: Row): Promise<[boolean, string]> {
  const criteria = s.text("BOT_AI_FILTER_CRITERIA");
  if (!criteria) return [true, "ai_filter:no_criteria"];
  const system = "You are a strict screening filter for a freelancer's auto-bidding bot. " +
    "Given the freelancer's criteria and a job post, decide whether they should bid. " +
    'Reply with ONLY a JSON object: {"bid": true|false, "reason": "<short reason>"}.';
  const user = `Freelancer's bidding criteria:\n${criteria}\n\nJob post:\nTitle: ${r.title}\nSkills: ${r.skills || "(none)"}\n` +
    `Budget: ${r.budget_min}-${r.budget_max} ${r.currency ?? ""}\nDescription:\n${r.description.slice(0, 4000)}\n\n` +
    "Should they bid? Respond with the JSON object only.";
  try {
    const resp = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: { Authorization: `Bearer ${s.str("OPENAI_API_KEY")}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: s.str("OPENAI_MODEL") || DEFAULTS.OPENAI_MODEL, input: [{ role: "system", content: system }, { role: "user", content: user }] }),
    });
    const data = await resp.json();
    if (!resp.ok) return [true, `ai_filter_error:HTTP${resp.status}`];
    const out = ((data.output ?? []) as Json[]).flatMap((o) => (o.content as Json[] | undefined) ?? [])
      .filter((c) => c.type === "output_text").map((c) => String(c.text ?? "")).join("").trim();
    const obj = extractJson(out);
    if (!("bid" in obj)) return [true, "ai_filter_error:unparseable"];
    const should = Boolean(obj.bid);
    return [should, `ai_filter:${should ? "accept" : "reject"}:${String(obj.reason ?? "").slice(0, 200)}`];
  } catch (e) {
    return [true, `ai_filter_error:${(e as Error).name}`];
  }
}

// ── Alerts (bot/telegram_notify.py, bot/slack_notify.py) ─────────────────────────
const esc = (v: unknown) => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function projectLink(url: string | null): string | null {
  const v = (url ?? "").trim();
  if (!v) return null;
  if (/^https?:\/\//.test(v)) return v;
  if (v.startsWith("/")) return `https://www.freelancer.com${v}`;
  return `https://www.freelancer.com/projects/${v}/details`;
}

function formatPosted(epoch: number | null): string | null {
  if (epoch == null) return null;
  const s = Math.max(0, Math.trunc(Date.now() / 1000 - epoch));
  const ago = s < 3600 ? `${Math.floor(s / 60)}m ago`
    : s < 86400 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`
    : `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h ago`;
  return `${new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC (${ago})`;
}

const flagsOf = (r: Row) => DISPLAY_UPGRADES.filter(([k]) => r.upgrades?.[k]).map(([, l]) => l);
const fmtNum = (n: number | null) => (n == null ? "-" : String(n));

function borderedTable(cells: [string, string][]): string {
  const widths = cells.map(([l, v]) => Math.max(l.length, v.length));
  const rule = (l: string, m: string, r: string) => l + widths.map((w) => "─".repeat(w + 2)).join(m) + r;
  const row = (vals: string[]) => "│" + vals.map((v, i) => ` ${v.padEnd(widths[i])} `).join("│") + "│";
  return [rule("┌", "┬", "┐"), row(cells.map(([l]) => l)), rule("├", "┼", "┤"), row(cells.map(([, v]) => v)), rule("└", "┴", "┘")].join("\n");
}

function telegramMessage(r: Row, c: Client | null, sc: number): string {
  const url = projectLink(r.url);
  const title = esc(r.title || "(no title)");
  const head = [`#${r.id}`, url ? `<b>Title:</b> <a href="${esc(url)}">${title}</a>` : `<b>Title:</b> ${title}`];
  if (url) head.push(`Link: <a href="${esc(url)}">${esc(url)}</a>`);
  if (r.type === "hourly") head.push("<b>Type:</b> ⏱️ Hourly");
  else if (r.type === "fixed") head.push("<b>Type:</b> 💲 Fixed-price");
  head.push(`<b>Skills:</b> ${esc(r.skills.trim() || "-")}`);
  const flags = flagsOf(r);
  if (flags.length) head.push(`<b>Flags:</b> 🏷️ ${esc(flags.join(", "))}`);

  const cells: [string, string][] = [];
  if (r.budget_min != null || r.budget_max != null) {
    cells.push(["Budget", `${fmtNum(r.budget_min)} - ${fmtNum(r.budget_max)} ${r.currency ?? ""}`.trimEnd() + (r.type === "hourly" ? " /hr" : "")]);
  }
  cells.push(["Score", String(sc)]);
  const posted = formatPosted(r.created_at);
  if (posted) cells.push(["Posted", posted]);
  cells.push(["Bid count", r.bid_count != null ? String(r.bid_count) : "-"]);
  cells.push(["Bid avg", r.bid_avg != null ? String(Math.round(r.bid_avg * 100) / 100) : "-"]);
  const country = c?.country_name || c?.country_code || (typeof c?.country === "string" ? c.country : null);
  cells.push(["Country", country ? String(country) : "-"]);
  cells.push(["Completed", c?.completed_jobs != null ? String(c.completed_jobs) : "-"]);
  cells.push(["Verified", c?.payment_verified === true ? "Yes" : c?.payment_verified === false ? "No" : "-"]);
  const header = head.join("\n") + "\n<pre>" + esc(borderedTable(cells)) + "</pre>";

  let desc = r.description.trim().replace(/\n{3,}/g, "\n\n");
  if (!desc && !r.skills.trim()) return header;
  const assemble = (d: string) => d ? `${header}\n\n<b>Description:</b>\n<blockquote expandable>${esc(d)}</blockquote>` : header;
  let msg = assemble(desc);
  if (desc && msg.length > 3900) {
    desc = desc.slice(0, Math.max(0, desc.length - (msg.length - 3900) - 16)).trimEnd() + " …";
    msg = assemble(desc);
  }
  return msg;
}

function slackMessage(r: Row, c: Client | null, sc: number): string {
  const md = (v: unknown) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const url = projectLink(r.url);
  const title = md(r.title || "(no title)");
  const lines = [url ? `*<${url}|${title}>*` : `*${title}*`, `\`#${r.id}\``];
  if (r.type === "hourly") lines.push("⏱️ Hourly");
  else if (r.type === "fixed") lines.push("💲 Fixed-price");
  const facts: string[] = [];
  if (r.budget_min != null || r.budget_max != null) {
    facts.push(`*Budget:* ${md(`${fmtNum(r.budget_min)} - ${fmtNum(r.budget_max)} ${r.currency ?? ""}`.trimEnd() + (r.type === "hourly" ? " /hr" : ""))}`);
  }
  facts.push(`*Score:* ${sc}`);
  const posted = formatPosted(r.created_at);
  if (posted) facts.push(`*Posted:* ${md(posted)}`);
  if (r.bid_count != null) facts.push(`*Bids:* ${r.bid_count}`);
  if (c) {
    const country = c.country_name || c.country_code || (typeof c.country === "string" ? c.country : null);
    if (country) facts.push(`*Country:* ${md(country)}`);
    if (c.completed_jobs != null) facts.push(`*Completed:* ${c.completed_jobs}`);
    facts.push(`*Verified:* ${c.payment_verified === true ? "Yes" : c.payment_verified === false ? "No" : "-"}`);
  }
  lines.push(facts.join(" · "));
  if (r.skills.trim()) lines.push(`*Skills:* ${md(r.skills.trim())}`);
  const flags = flagsOf(r);
  if (flags.length) lines.push(`*Flags:* 🏷️ ${md(flags.join(", "))}`);
  return lines.join("\n");
}

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

/** sendMessage with short retries on 429 / 5xx / network errors (a failure that
 *  outlives them is retried next cycle as status alert_failed). */
async function sendTelegram(token: string, chatId: string, html: string): Promise<void> {
  for (let attempt = 1; ; attempt++) {
    let retryAfter = 0;
    let err: string;
    let r: Response | null = null;
    try {
      r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text: html, parse_mode: "HTML", disable_web_page_preview: true }),
      });
    } catch (e) {
      err = `Telegram request failed: ${(e as Error).message}`;
    }
    if (r) {
      if (r.ok) return;
      const body = await r.text();
      err = `Telegram HTTP ${r.status}: ${body.slice(0, 200)}`;
      if (r.status !== 429 && r.status < 500) throw new Error(err); // e.g. 400: retrying won't help
      try { retryAfter = Number(JSON.parse(body)?.parameters?.retry_after) || 0; } catch { /* none */ }
    }
    if (attempt >= 3) throw new Error(err!);
    await sleep(Math.min(10_000, retryAfter ? retryAfter * 1000 + 500 : 1000 * 2 ** attempt));
  }
}

async function sendSlack(webhook: string, text: string): Promise<boolean> {
  try {
    const r = await fetch(webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, unfurl_links: false, unfurl_media: false }),
    });
    const body = (await r.text()).trim().toLowerCase();
    if (body !== "ok") console.log(`[slack] rejected: ${body || r.status}`);
    return body === "ok";
  } catch (e) {
    console.log(`[slack] send failed: ${(e as Error).message}`);
    return false;
  }
}

/** True when the alert reached at least one destination. */
async function alert(s: Settings, r: Row, c: Client | null, sc: number): Promise<{ sent: boolean; error?: string }> {
  const slack = s.str("SLACK_WEBHOOK_URL");
  let sent = slack ? await sendSlack(slack, slackMessage(r, c, sc)) : false;
  let error: string | undefined;
  const html = telegramMessage(r, c, sc);
  for (const [token, chat] of s.telegramTargets()) {
    try {
      await sendTelegram(token, chat, html);
      sent = true;
    } catch (e) {
      error = (e as Error).message;
      console.log(`[telegram] send to ${chat} failed: ${error}`);
    }
  }
  return { sent, error };
}

// ── The cycle ────────────────────────────────────────────────────────────────────
function jobRecord(r: Row, sc: number, status: string, reason: string | null) {
  return {
    id: r.id, title: r.title, url: r.url, description: r.description, currency: r.currency,
    budget_min: r.budget_min, budget_max: r.budget_max, bid_count: r.bid_count, bid_avg: r.bid_avg,
    skills: r.skills, posted_at: r.created_at != null ? new Date(r.created_at * 1000).toISOString() : null,
    bidperiod: r.bidperiod != null ? Math.trunc(r.bidperiod) : null, upgrades: r.upgrades, project_type: r.type,
    score: sc, status, filter_reason: reason, synced_at: new Date().toISOString(),
  };
}

async function pool<T>(items: T[], n: number, fn: (x: T) => Promise<void>) {
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (i < items.length) await fn(items[i++]);
  }));
}

type Tally = { stored: number; alerted: number; failed: number };

async function cycle(db: SupabaseClient, s: Settings, floor: number): Promise<Tally> {
  const t: Tally = { stored: 0, alerted: 0, failed: 0 };
  const notifyOn = s.bool("BOT_NOTIFY_ENABLED", true) && (s.telegramTargets().length > 0 || !!s.str("SLACK_WEBHOOK_URL"));
  const windowOpen = withinWindow(s.str("BOT_NOTIFY_START"), s.str("BOT_NOTIFY_END"), nowMinutes(s.float("BOT_ACTIVE_TZ_OFFSET")));
  const aiOn = s.bool("BOT_AI_FILTER_ENABLED", false) && !!s.str("OPENAI_API_KEY") && !!s.text("BOT_AI_FILTER_CRITERIA");
  const minScore = s.int("BOT_MIN_SCORE");
  const owners = new Map<number | null, Promise<Client>>();
  const tried = new Set<number>(); // alerted this cycle: not retried until the next
  const save = async (rec: ReturnType<typeof jobRecord>) => {
    const { error } = await db.from("jobs").upsert(rec, { onConflict: "id" });
    if (error) throw new Error(`jobs write failed: ${error.message}`);
  };

  const handle = async (r: Row) => {
    const now = Date.now() / 1000;
    const cheap = cheapFilter(s, r, now);
    // Skipped currencies, old projects and closing bids are never stored.
    if (cheap && /^(currency_skipped|project_too_old|bid_ending_soon)/.test(cheap)) return;
    if (cheap) return save(jobRecord(r, 0, "filtered", cheap));

    if (!owners.has(r.owner_id)) owners.set(r.owner_id, clientStatus(s, r.owner_id));
    const client = await owners.get(r.owner_id)!;
    const byClient = clientFilter(s, client);
    if (byClient) return save(jobRecord(r, 0, "filtered", byClient));

    if (aiOn) {
      const [ok, why] = await aiFilter(s, r);
      if (!ok) return save(jobRecord(r, 0, "filtered", why));
    }

    const sc = score(s, r);
    if (sc < minScore) return save(jobRecord(r, sc, "skipped", null));
    // Never alert on jobs posted before fetching (re)started: they are a backlog.
    if (r.created_at != null && r.created_at < floor) return save(jobRecord(r, sc, "filtered", "posted_before_session_start"));
    t.stored++;
    if (!notifyOn) return save(jobRecord(r, sc, "alerted", null)); // nothing to send to
    if (!windowOpen) return save(jobRecord(r, sc, "notify_skipped", null));

    // Stored first so the Jobs page has it before the alert points at it.
    await save(jobRecord(r, sc, "alerting", null));
    tried.add(r.id);
    const out = await alert(s, r, client, sc);
    await db.from("jobs").update({ status: out.sent ? "alerted" : "alert_failed", synced_at: new Date().toISOString() }).eq("id", r.id);
    if (out.sent) t.alerted++; else t.failed++;
  };

  // Newest first (the search's default order — don't pass a sort, see collector.py);
  // a page with nothing unseen means we've caught up.
  const query = s.list("BOT_KEYWORDS").join(" ");
  const started = Date.now();
  for (let page = 0, offset = 0; page < MAX_PAGES && Date.now() - started < MAX_FETCH_MS; page++, offset += PAGE_SIZE) {
    const res = await flnGet(s, "projects/0.1/projects/active/", {
      query, limit: PAGE_SIZE, offset, full_description: "true", job_details: "true",
    });
    const projects = ((res.projects ?? []) as Json[]).map(toRow).filter((r) => r.id > 0);
    if (!projects.length) break;
    const { data: seenRows, error } = await db.from("fetch_seen").select("id").in("id", projects.map((r) => r.id));
    if (error) throw new Error(`fetch_seen read failed: ${error.message}`);
    const seen = new Set((seenRows ?? []).map((x) => Number(x.id)));
    const fresh = projects.filter((r) => !seen.has(r.id));
    if (fresh.length) {
      await pool(fresh, CONCURRENCY, async (r) => {
        try { await handle(r); } catch (e) { console.log(`[${r.id}] failed: ${(e as Error).message}`); }
      });
      // Marked after handling, so a crash mid-page retries those projects.
      await db.from("fetch_seen").upsert(fresh.map((r) => ({ id: r.id })), { onConflict: "id", ignoreDuplicates: true });
    }
    if (!fresh.length || projects.length < PAGE_SIZE) break;
  }

  // Retry alerts that failed in an earlier cycle (until they age out).
  if (notifyOn && windowOpen) {
    const { data: failed } = await db.from("jobs").select("*").eq("status", "alert_failed").limit(20);
    const maxAge = s.int("BOT_MAX_PROJECT_AGE_SECONDS");
    for (const j of failed ?? []) {
      if (tried.has(Number(j.id))) continue;
      const posted = j.posted_at ? Date.parse(j.posted_at) / 1000 : null;
      if (maxAge > 0 && posted != null && Date.now() / 1000 - posted > maxAge) {
        await db.from("jobs").update({ status: "filtered", filter_reason: `project_too_old:${Math.trunc(Date.now() / 1000 - posted)}s` }).eq("id", j.id);
        continue;
      }
      // Claim it, so an overlapping run can't send it twice.
      const { data: claimed } = await db.from("jobs").update({ status: "alerting" }).eq("id", j.id).eq("status", "alert_failed").select("id");
      if (!claimed?.length) continue;
      const r: Row = {
        id: Number(j.id), title: j.title ?? "", url: j.url, description: j.description ?? "", currency: j.currency,
        budget_min: j.budget_min != null ? Number(j.budget_min) : null, budget_max: j.budget_max != null ? Number(j.budget_max) : null,
        bid_count: j.bid_count, bid_avg: j.bid_avg != null ? Number(j.bid_avg) : null, skills: j.skills ?? "",
        created_at: posted, bidperiod: j.bidperiod, owner_id: null, upgrades: j.upgrades, type: j.project_type,
      };
      const out = await alert(s, r, null, j.score ?? 0);
      await db.from("jobs").update({ status: out.sent ? "alerted" : "alert_failed", synced_at: new Date().toISOString() }).eq("id", j.id);
      if (out.sent) t.alerted++; else t.failed++;
    }
  }

  await db.from("fetch_seen").delete().lt("seen_at", new Date(Date.now() - SEEN_DAYS * 86400_000).toISOString());
  return t;
}

async function run(): Promise<string> {
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false },
  });
  const { data: rows, error } = await db.from("app_settings").select("key,value");
  if (error) throw new Error(`settings read failed: ${error.message}`);
  const s = new Settings({ ...DEFAULTS, ...Object.fromEntries((rows ?? []).map((r) => [r.key, r.value ?? ""])) });
  const interval = Math.max(1, s.int("BOT_POLL_INTERVAL_SECONDS"));

  const { data: claim, error: claimErr } = await db.rpc("begin_fetch_cycle", { p_min_interval: interval, p_lease: LEASE_SECONDS });
  if (claimErr) throw new Error(`cycle claim failed: ${claimErr.message} (run supabase/schema.sql)`);
  const state = (claim as Json[] | null)?.[0];
  if (!state) return "not due";

  const finish = (message: string, patch: Json = {}) =>
    db.from("worker_status").update({ last_cycle_at: new Date().toISOString(), message: message.slice(0, 500), lock_until: null, ...patch }).eq("id", 1);

  try {
    // Paused or outside hours: the next fetch starts a fresh alert floor, so jobs
    // posted meanwhile aren't replayed as a backlog.
    if (!s.bool("BOT_FETCH_ENABLED", true)) {
      await finish("Fetching paused (Fetch jobs is off).", { alert_floor_at: null });
      return "paused";
    }
    const offset = s.float("BOT_ACTIVE_TZ_OFFSET");
    const now = nowMinutes(offset);
    if (!withinWindow(s.str("BOT_ACTIVE_START"), s.str("BOT_ACTIVE_END"), now)) {
      const msg = `Outside active hours (${s.str("BOT_ACTIVE_START")}-${s.str("BOT_ACTIVE_END")}, now ${fmtMinutes(now)} UTC${offset == null ? "" : (offset >= 0 ? "+" : "") + offset}).`;
      await finish(msg, { alert_floor_at: null });
      return "outside hours";
    }
    if (!s.str("FLN_OAUTH_TOKEN")) {
      await finish("No Freelancer OAuth token: set it in Admin › Bot settings.");
      return "no token";
    }

    // Alert floor: reset on the first run and after any gap (fetcher down), like
    // the Python worker's launch time.
    const lastFetch = state.last_fetch_at ? Date.parse(String(state.last_fetch_at)) : 0;
    const gap = Date.now() - lastFetch > Math.max(600, 3 * interval) * 1000;
    const floorAt = !state.alert_floor_at || gap ? new Date().toISOString() : String(state.alert_floor_at);

    const t = await cycle(db, s, Date.parse(floorAt) / 1000);
    const msg = `Stored ${t.stored} new job(s), alerted ${t.alerted}` + (t.failed ? `, ${t.failed} alert(s) failed (will retry)` : "") + ".";
    await finish(msg, { alert_floor_at: floorAt, last_fetch_at: new Date().toISOString() });
    return msg;
  } catch (e) {
    await finish(`Cycle failed: ${(e as Error).message}`);
    throw e;
  }
}

declare const EdgeRuntime: { waitUntil(p: Promise<unknown>): void } | undefined;

Deno.serve((req) => {
  const secret = Deno.env.get("FETCH_JOBS_SECRET");
  if (!secret || req.headers.get("x-fetch-secret") !== secret) {
    return new Response(JSON.stringify({ ok: false, message: "Forbidden" }), { status: 403 });
  }
  // Answer the cron call at once and keep working in the background, so pg_net's
  // short request timeout never cuts a cycle short.
  const work = run().then((m) => console.log(m), (e) => console.log(`cycle failed: ${(e as Error).message}`));
  if (typeof EdgeRuntime !== "undefined") {
    EdgeRuntime.waitUntil(work);
    return new Response(JSON.stringify({ ok: true, started: true }), { headers: { "Content-Type": "application/json" } });
  }
  return work.then(() => new Response(JSON.stringify({ ok: true })));
});
