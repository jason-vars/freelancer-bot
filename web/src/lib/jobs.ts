import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { SKIPPABLE_UPGRADES } from "@/lib/settings/fields";
import { getEffectiveSettings, isTrue, type Values } from "@/lib/settings/store";
import { aiPriceAndDuration, generateProposal } from "@/lib/proposal";

// Server-side job actions shared by the Jobs page and the userscript API. Ports of
// the matching bot/webui.py helpers (_generate_for_job, _mark_applied, _job_text).

export type Job = {
  id: number;
  title: string;
  url: string | null;
  description: string | null;
  currency: string | null;
  budget_min: number | null;
  budget_max: number | null;
  bid_count: number | null;
  bid_avg: number | null;
  skills: string | null;
  posted_at: string | null;
  bidperiod: number | null;
  upgrades: Record<string, unknown> | null;
  // "fixed" | "hourly" (null = unknown, treated as fixed).
  project_type: string | null;
  score: number;
  status: string;
  filter_reason: string | null;
};

export type JobRef = { id?: number | null; seo?: string | null };
type Result = { status: number; body: Record<string, unknown> };

const csv = (v: string | undefined) => (v ?? "").split(",").map((x) => x.trim()).filter(Boolean);
const lines = (v: string | undefined) => (v ?? "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
const int = (v: string | undefined, d: number) => (/^\d+$/.test((v ?? "").trim()) ? parseInt(v!, 10) : d);

export function jobUrl(seo: string | null): string {
  if (!seo) return "";
  if (/^https?:\/\//.test(seo)) return seo;
  return `https://www.freelancer.com/projects/${seo.replace(/^\/+/, "")}`;
}

/** Portfolio entries, one per line; a legacy comma-separated list still works. */
function portfolio(v: string | undefined): string[] {
  const text = (v ?? "").trim();
  if (!text) return [];
  const parts = text.includes("\n") || text.includes("|") ? text.split(/\r?\n/) : text.split(",");
  return parts.map((p) => p.trim()).filter(Boolean);
}

// ── Freelancer API (admin's token) ───────────────────────────────────────────────
function freelancerBase(s: Values): string {
  return (s.FLN_URL || "https://www.freelancer.com").replace(/\/+$/, "");
}

function projectToJob(p: Record<string, unknown>): Job {
  const budget = (p.budget ?? {}) as Record<string, unknown>;
  const cur = p.currency as Record<string, unknown> | string | undefined;
  const currency = (typeof cur === "object" && cur ? (cur.code as string) : (cur as string)) ||
    ((budget.currency as Record<string, unknown> | undefined)?.code as string) || null;
  const stats = (p.bid_stats ?? {}) as Record<string, unknown>;
  const submitted = p.time_submitted ?? p.date_submitted ?? p.time_created;
  return {
    id: Number(p.id),
    title: String(p.title ?? ""),
    url: (p.seo_url as string) ?? (p.url as string) ?? null,
    description: String(p.description ?? p.preview_description ?? ""),
    currency,
    budget_min: (budget.minimum as number) ?? null,
    budget_max: (budget.maximum as number) ?? null,
    bid_count: (stats.bid_count as number) ?? null,
    bid_avg: (stats.bid_avg as number) ?? null,
    skills: ((p.jobs as Record<string, unknown>[] | undefined) ?? []).map((j) => String(j.name)).join(","),
    posted_at: typeof submitted === "number" ? new Date(submitted * 1000).toISOString() : null,
    bidperiod: typeof p.bidperiod === "number" ? p.bidperiod : null,
    upgrades: (p.upgrades as Record<string, unknown>) ?? null,
    project_type: p.type === "hourly" || p.type === "fixed" ? p.type : null,
    score: 0,
    status: "external",
    filter_reason: null,
  };
}

async function fetchLiveProject(s: Values, id: number): Promise<Job | null> {
  if (!s.FLN_OAUTH_TOKEN) return null;
  const url = `${freelancerBase(s)}/api/projects/0.1/projects/${id}/?full_description=true&job_details=true&upgrade_details=true`;
  try {
    const r = await fetch(url, { headers: { "freelancer-oauth-v1": s.FLN_OAUTH_TOKEN }, cache: "no-store" });
    if (!r.ok) return null;
    const data = await r.json();
    const p = data?.result;
    return p && p.id ? projectToJob(p) : null;
  } catch {
    return null;
  }
}

// ── Lookups ──────────────────────────────────────────────────────────────────────
async function findJob(ref: JobRef): Promise<Job | null> {
  const db = createAdminClient();
  const q = db.from("jobs").select("*");
  const { data } = ref.id ? await q.eq("id", ref.id).maybeSingle()
    : ref.seo ? await q.eq("url", ref.seo).limit(1).maybeSingle()
    : { data: null };
  return (data as Job | null) ?? null;
}

/** The stored job, or — for a project the worker never collected — fetched live and
 *  stored with status 'external' so it can carry the user's opened/applied state. */
async function ensureJob(s: Values, ref: JobRef): Promise<{ job: Job | null; collected: boolean; error?: Result }> {
  const stored = await findJob(ref);
  if (stored) return { job: stored, collected: true };
  if (!ref.id) {
    return { job: null, collected: false, error: { status: 404, body: { ok: false,
      message: `Couldn't read the Project ID from the page for '${ref.seo}', and the bot hasn't collected it. Reload the project page and try again.` } } };
  }
  const live = await fetchLiveProject(s, ref.id);
  if (!live) return { job: null, collected: false, error: { status: 404, body: { ok: false, message: `Project ${ref.id} not found on Freelancer.` } } };
  await createAdminClient().from("jobs").upsert(live, { onConflict: "id", ignoreDuplicates: true });
  return { job: live, collected: false };
}

async function touchUserJob(userId: string, jobId: number, patch: Record<string, unknown>) {
  const db = createAdminClient();
  const { data: existing } = await db.from("user_jobs").select("opened_at")
    .eq("user_id", userId).eq("job_id", jobId).maybeSingle();
  const row: Record<string, unknown> = { user_id: userId, job_id: jobId, ...patch };
  if (!existing?.opened_at) row.opened_at = new Date().toISOString();
  await db.from("user_jobs").upsert(row, { onConflict: "user_id,job_id" });
}

// ── Filters (the cheap ones that can be judged from the project itself) ─────────
function skipReason(s: Values, j: Job, clientCountry: string | null, collected: boolean): string | null {
  const code = (j.currency ?? "").trim().toUpperCase();
  if (code && csv(s.BOT_SKIP_CURRENCIES).map((c) => c.toUpperCase()).includes(code)) return `currency_skipped:${code}`;

  const up = j.upgrades ?? {};
  for (const [suffix, key] of SKIPPABLE_UPGRADES) {
    if (isTrue(s[`BOT_SKIP_${suffix}`]) && up[key]) return `upgrade_blocked:${key}`;
  }

  if (!collected) {
    // A collected job already passed the worker's full filter set; a live-fetched one
    // gets the payload checks here (same as _live_filter_skip_reason).
    const maxBudget = j.budget_max ?? j.budget_min;
    const minUsd = int(s.BOT_MIN_BUDGET_USD, 0);
    if (maxBudget != null && maxBudget < minUsd) return `budget_too_low:${maxBudget}`;
    const badges = (j.skills ?? "").split(",").map((b) => b.trim().toLowerCase()).filter(Boolean);
    for (const ex of csv(s.BOT_EXCLUDE_SKILLS).map((x) => x.toLowerCase())) {
      if (badges.some((b) => b === ex || b.includes(ex))) return `skill_blocked:${ex}`;
    }
    const title = (j.title ?? "").toLowerCase();
    for (const k of csv(s.BOT_EXCLUDE_TITLE_KEYWORDS).map((x) => x.toLowerCase())) if (title.includes(k)) return `title_keyword_blocked:${k}`;
    const desc = (j.description ?? "").toLowerCase();
    for (const k of csv(s.BOT_EXCLUDE_DESC_KEYWORDS).map((x) => x.toLowerCase())) if (desc.includes(k)) return `desc_keyword_blocked:${k}`;
  }

  // Country, matched by NAME inside the "About the Client" text the userscript scraped.
  const text = (clientCountry ?? "").toLowerCase();
  const allow = csv(s.BOT_ALLOW_COUNTRIES);
  const block = csv(s.BOT_SKIP_COUNTRIES);
  if (text && (allow.length || block.length)) {
    const inText = (name: string) => new RegExp("\\b" + name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b").test(text);
    if (block.some(inText)) return "country_blocked";
    if (allow.length && !allow.some(inText)) return "country_not_allowed";
  }
  return null;
}

/** Why the user's own filters (My settings › My job filters) hide this job, or null.
 *  Must match public.user_filter_reason() in supabase/schema.sql, which applies the
 *  same rules to the Jobs page. */
function userFilterReason(s: Values, j: Job): string | null {
  const lc = (v: string | undefined) => csv(v).map((x) => x.toLowerCase());
  const title = (j.title ?? "").toLowerCase();
  const descr = (j.description ?? "").toLowerCase();
  const skills = (j.skills ?? "").toLowerCase();
  const wanted = lc(s.MY_KEYWORDS);
  if (wanted.length && !wanted.some((k) => `${title} ${descr} ${skills}`.includes(k))) return "none of my keywords";
  for (const k of lc(s.MY_EXCLUDE_TITLE)) if (title.includes(k)) return `title has "${k}"`;
  for (const k of lc(s.MY_EXCLUDE_DESC)) if (descr.includes(k)) return `description has "${k}"`;
  for (const k of lc(s.MY_EXCLUDE_SKILLS)) if (skills.includes(k)) return `skill "${k}"`;
  if (lc(s.MY_SKIP_CURRENCIES).includes((j.currency ?? "").toLowerCase())) return `currency ${j.currency}`;
  const minBudget = int(s.MY_MIN_BUDGET, 0);
  const budget = j.budget_max ?? j.budget_min;
  if (minBudget > 0 && budget != null && budget < minBudget) return `budget below ${minBudget}`;
  const maxBids = int(s.MY_MAX_BIDS, 0);
  if (maxBids > 0 && (j.bid_count ?? 0) > maxBids) return `more than ${maxBids} bids`;
  return null;
}

/** "marked bad by 2 users (spam; fake budget)" when anyone flagged the job, else null. */
async function badMark(jobId: number): Promise<string | null> {
  const { data } = await createAdminClient().from("job_flags").select("reason").eq("job_id", jobId);
  if (!data?.length) return null;
  const reasons = [...new Set(data.map((f) => String(f.reason ?? "").trim()).filter(Boolean))];
  return `marked bad by ${data.length} user${data.length === 1 ? "" : "s"}` + (reasons.length ? ` (${reasons.join("; ")})` : "");
}

// ── Pricing: AI pricing (admin) → user's bid rules → half-way through the budget ─
function bidFromRules(s: Values, j: Job): [number, number] | null {
  let rules: Record<string, unknown>[] = [];
  try {
    rules = JSON.parse(s.BOT_BID_RULES || "[]");
  } catch {
    return null;
  }
  const budget = j.budget_max ?? j.budget_min;
  if (budget == null || !Array.isArray(rules)) return null;
  const code = (j.currency ?? "").trim().toUpperCase();
  const usable: [number, number, number, number][] = [];
  for (const r of rules) {
    const codes = ((r.currencies as string[]) ?? []).map((c) => String(c).trim().toUpperCase()).filter(Boolean);
    if (codes.length && code && !codes.includes(code)) continue;
    // Days round UP: "7.5" means 8, never 7.
    const [lo, hi, bid, delivery] = [Number(r.min), Number(r.max), Number(r.bid), Math.ceil(Number(r.delivery))];
    if ([lo, hi, bid, delivery].some(Number.isNaN)) continue;
    usable.push([lo, hi, bid, Math.max(1, delivery)]);
  }
  // Neighbouring rows share an edge (250-750, 750-1500), so a $250-750 job's top
  // (750) sits in both. Prefer the row that holds the job's WHOLE range, so the
  // answer doesn't depend on the order the rows were typed in; fall back to the
  // first row holding the top for budgets that straddle rows.
  const jobLo = j.budget_min ?? budget;
  const whole = usable.find(([lo, hi]) => lo <= jobLo && budget <= hi);
  if (whole) return [whole[2], whole[3]];
  const top = usable.find(([lo, hi]) => lo <= budget && budget <= hi);
  return top ? [top[2], top[3]] : null;
}

/** Round a bid UP to a clean number a person would type: 5s under 100, 50s under
 *  1000, 100s above (1125 -> 1200, 140 -> 150, 17.5 -> 20). Never above `cap` (the
 *  budget's max), so rounding can't push a bid over the client's range. */
function niceAmount(amount: number, cap?: number): number {
  const step = amount < 100 ? 5 : amount < 1000 ? 50 : 100;
  const nice = Math.ceil(amount / step) * step;
  return cap != null && nice > cap ? cap : nice;
}

async function resolvePricing(s: Values, j: Job): Promise<[number, number]> {
  if (isTrue(s.BOT_AI_PRICING_ENABLED) && s.OPENAI_API_KEY && (s.BOT_AI_PRICING_RULES ?? "").trim()) {
    const priced = await aiPriceAndDuration(s.OPENAI_API_KEY, s.OPENAI_MODEL || "gpt-5.2-mini", s.BOT_AI_PRICING_RULES, {
      title: j.title, description: j.description ?? "", skills: j.skills ?? "",
      budgetMin: j.budget_min, budgetMax: j.budget_max, currency: j.currency,
    });
    if (priced) return priced;
  }
  const { budget_min: lo, budget_max: hi } = j;
  // Hourly: bid your hourly rate. The bid table holds fixed-price totals, so a
  // $40-50/hr job would otherwise match a 30-250 row and bid $200 an hour. The rate
  // is raised to the client's minimum (a bid below their range looks like a mistake)
  // but never lowered to their maximum. No rate set: middle of their range.
  if (j.project_type === "hourly") {
    const rate = Number(s.BOT_HOURLY_RATE);
    const amount = rate > 0 ? Math.max(rate, lo ?? 0)
      : lo == null && hi == null ? 200 : hi == null ? niceAmount(lo!) : lo == null ? hi : niceAmount((lo + hi) / 2, hi);
    return [amount, Math.max(1, int(s.BOT_DEFAULT_PERIOD_DAYS, 7))];
  }
  const ruled = bidFromRules(s, j);
  if (ruled) return ruled;
  const amount = lo == null && hi == null ? 200 : hi == null ? niceAmount(lo!) : lo == null ? hi : niceAmount((lo + hi) / 2, hi);
  const top = hi ?? lo;
  const days = top != null && top < 300 ? 1 : int(s.BOT_DEFAULT_PERIOD_DAYS, 7);
  return [amount, days];
}

// ── Actions ──────────────────────────────────────────────────────────────────────
/** Generate a proposal for one user. Same contract as the Python /jobs/generate,
 *  so the userscript needs no changes beyond its base URL and API key. */
// force: skip every filter and the "already applied" guard (the web Jobs page).
// manual: the userscript's ✨ Generate button — skips only the user's OWN filters.
export async function generateForUser(userId: string, ref: JobRef, clientCountry: string | null, opts: { force?: boolean; manual?: boolean } = {}): Promise<Result> {
  const s = await getEffectiveSettings(userId);
  const { job, collected, error } = await ensureJob(s, ref);
  if (error || !job) return error!;

  if (!opts.force) {
    const { data: mine } = await createAdminClient().from("user_jobs").select("applied_at")
      .eq("user_id", userId).eq("job_id", job.id).maybeSingle();
    if (mine?.applied_at) {
      return { status: 200, body: { ok: false, skipped: true, already_applied: true, message: "You've already applied to this project — not regenerating." } };
    }
    const skip = skipReason(s, job, clientCountry, collected);
    const personal = skip || opts.manual ? null : userFilterReason(s, job);
    const bad = skip || personal || opts.manual ? null : await badMark(job.id);
    if (skip || personal || bad) {
      await touchUserJob(userId, job.id, {});
      const message = skip
        ? `Skipped — matches the filters (${skip}); no proposal generated.`
        : personal
          ? `Skipped — hidden by your own filters (${personal}); no proposal generated. Press ✨ Generate to write one anyway.`
          : `Skipped — ${bad}; no proposal generated. Press ✨ Generate to write one anyway.`;
      return { status: 200, body: { ok: false, skipped: true, message } };
    }
  }
  if (!s.OPENAI_API_KEY) {
    return { status: 400, body: { ok: false, message: "No OpenAI API key is set — ask the admin to add one in Admin › Settings." } };
  }

  let proposal: string;
  try {
    proposal = await generateProposal(s.OPENAI_API_KEY, s.OPENAI_MODEL || "gpt-5.2-mini", {
      title: job.title, description: job.description ?? "", skills: job.skills ?? "",
      budgetMin: job.budget_min, budgetMax: job.budget_max, currency: job.currency,
      profileBullets: lines(s.BOT_PROFILE_BULLETS),
      portfolioUrls: portfolio(s.BOT_PORTFOLIO_URLS),
      signatureName: (s.BOT_SIGNATURE_NAME ?? "").trim(),
      extraInstructions: s.BOT_PROPOSAL_INSTRUCTIONS ?? "",
      includeName: isTrue(s.BOT_INCLUDE_NAME),
      includeProfile: isTrue(s.BOT_INCLUDE_PROFILE),
      askQuestion: isTrue(s.BOT_ASK_QUESTION),
      template: s.BOT_PROPOSAL_TEMPLATE ?? "",
      prefix: s.BOT_PROPOSAL_PREFIX ?? "",
      suffix: s.BOT_PROPOSAL_SUFFIX ?? "",
      prefixInline: isTrue(s.BOT_PROPOSAL_PREFIX_INLINE),
    });
  } catch (e) {
    const err = e as Error;
    return { status: 400, body: { ok: false, message: `OpenAI generation failed: ${err.name}: ${String(err.message).slice(0, 200)}` } };
  }
  const [amount, period] = await resolvePricing(s, job);
  await touchUserJob(userId, job.id, {
    proposal, amount, period_days: period, generated_at: new Date().toISOString(),
  });
  return {
    status: 200,
    body: {
      ok: true, id: job.id, proposal, amount, period, currency: job.currency ?? "",
      milestone: int(s.BOT_DEFAULT_MILESTONE_PERCENT, 50),
      seal: isTrue(s.BOT_SEAL_BIDS), autogen: isTrue(s.BOT_AUTO_GENERATE), autobid: isTrue(s.BOT_AUTO_BID),
    },
  };
}

export async function markOpened(userId: string, ref: JobRef): Promise<Result> {
  const s = await getEffectiveSettings(userId);
  const { job, error } = await ensureJob(s, ref);
  if (error || !job) return error!;
  await touchUserJob(userId, job.id, {});
  return { status: 200, body: { ok: true } };
}

export async function markApplied(userId: string, ref: JobRef, applied = true): Promise<Result> {
  const s = await getEffectiveSettings(userId);
  const { job, error } = await ensureJob(s, ref);
  if (error || !job) return error!;
  await touchUserJob(userId, job.id, { applied_at: applied ? new Date().toISOString() : null });
  return { status: 200, body: { ok: true, status: applied ? "applied" : "opened" } };
}

/** Mark many stored jobs applied at once (the Jobs page's "Mark all as applied").
 *  Keeps each job's first opened time; unknown ids are ignored. */
export async function markAppliedMany(userId: string, ids: number[]): Promise<Result> {
  const db = createAdminClient();
  const { data: found, error } = await db.from("jobs").select("id").in("id", ids);
  if (error) return { status: 400, body: { ok: false, message: error.message } };
  const jobIds = (found ?? []).map((j) => Number(j.id));
  if (!jobIds.length) return { status: 200, body: { ok: true, count: 0 } };
  const { data: existing } = await db.from("user_jobs").select("job_id,opened_at")
    .eq("user_id", userId).in("job_id", jobIds);
  const opened = new Map((existing ?? []).map((r) => [Number(r.job_id), r.opened_at as string | null]));
  const now = new Date().toISOString();
  const rows = jobIds.map((id) => ({ user_id: userId, job_id: id, applied_at: now, opened_at: opened.get(id) || now }));
  const { error: upErr } = await db.from("user_jobs").upsert(rows, { onConflict: "user_id,job_id" });
  if (upErr) return { status: 400, body: { ok: false, message: upErr.message } };
  return { status: 200, body: { ok: true, count: jobIds.length } };
}

export async function jobText(userId: string, ref: JobRef): Promise<Result> {
  const s = await getEffectiveSettings(userId);
  const { job, error } = await ensureJob(s, ref);
  if (error || !job) return error!;
  return { status: 200, body: { ok: true, id: job.id, title: job.title, skills: job.skills ?? "", description: job.description ?? "", url: jobUrl(job.url) } };
}
