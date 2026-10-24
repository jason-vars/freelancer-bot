"use client";

import { createClient } from "@/lib/supabase/client";
import { DISPLAY_UPGRADES } from "@/lib/settings/fields";
import { jobUrl } from "@/lib/jobUrl";
import type { JobRow } from "./JobList";
import { PAGE_SIZE, PASSED, type View } from "./views";

// Loads one Jobs tab straight from Supabase in the browser, as the signed-in user
// (RLS and the my_jobs view decide what they see). Live updates re-run this instead
// of re-rendering the page on the web host, so new jobs cost the host nothing.

/** How long after posting the bot saved the job: "40s", "3m", "1h 5m". */
function foundAfter(posted: string | null, found: string | null): string {
  if (!posted || !found) return "";
  const s = Math.max(0, Math.round((new Date(found).getTime() - new Date(posted).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function fmtBudget(min: number | null, max: number | null, cur: string | null): string {
  const n = (x: number | null) => (x == null ? "" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 }));
  if (min == null && max == null) return "—";
  return `${n(min)}${max != null && max !== min ? `–${n(max)}` : ""} ${cur ?? ""}`.trim();
}

export type JobsResult = { jobs: JobRow[]; total: number; error: string };

export async function loadJobs(view: View, search: string, page: number, isAdmin: boolean): Promise<JobsResult> {
  const supabase = createClient();
  let q = supabase
    .from("my_jobs")
    // No description: it's most of each row's size. Job loads it when its panel opens.
    .select("id,title,url,currency,budget_min,budget_max,bid_count,bid_avg,skills,posted_at,found_at,upgrades,score,status,filter_reason,opened_at,applied_at,skipped_at,proposal,amount,period_days,my_filter_reason,bad_count,bad_by_me,bad_reasons", { count: "exact" });
  // Each job sits in one of New / Opened / Applied / Skipped (your own state), and
  // "All" lists every match. They apply your own filters (My settings) too: what those
  // hide is under "Skipped". Jobs anyone marked bad leave everyone's lists and move to
  // "Marked bad". JobFeed's belongsIn() must agree, so a change moves a card at once.
  if (view === "new") q = q.in("status", PASSED).is("my_filter_reason", null).eq("bad_count", 0)
    .is("opened_at", null).is("applied_at", null).is("skipped_at", null);
  else if (view === "opened") q = q.not("opened_at", "is", null).is("applied_at", null).is("skipped_at", null).eq("bad_count", 0);
  else if (view === "applied") q = q.not("applied_at", "is", null);
  else if (view === "passed") q = q.in("status", PASSED).is("my_filter_reason", null).eq("bad_count", 0);
  else if (view === "skipped") q = q.is("applied_at", null).eq("bad_count", 0)
    .or(`skipped_at.not.is.null,and(status.in.(${PASSED.join(",")}),my_filter_reason.not.is.null)`);
  else if (view === "bad") q = q.gt("bad_count", 0);
  else if (view === "filtered") q = q.in("status", ["filtered", "skipped"]);
  if (search) q = q.ilike("title", `%${search.replace(/[%_]/g, "")}%`);
  const { data, count, error } = await q
    .order("posted_at", { ascending: false, nullsFirst: false })
    .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE - 1);
  if (error) return { jobs: [], total: 0, error: error.message };

  const rows = data ?? [];
  let activity: Record<number, { opened: number; applied: number }> = {};
  if (isAdmin && rows.length) {
    const { data: act } = await supabase.from("job_activity").select("*").in("job_id", rows.map((r) => r.id));
    activity = Object.fromEntries((act ?? []).map((a) => [a.job_id, { opened: a.opened_count, applied: a.applied_count }]));
  }

  const jobs: JobRow[] = rows.map((r) => ({
    id: r.id,
    title: r.title || `Project ${r.id}`,
    url: jobUrl(r.url),
    skills: (r.skills ?? "").split(",").map((s: string) => s.trim()).filter(Boolean),
    budget: fmtBudget(r.budget_min, r.budget_max, r.currency),
    bids: r.bid_count != null ? `${r.bid_count} bids${r.bid_avg ? ` · avg ${Math.round(r.bid_avg)}` : ""}` : "",
    posted: ago(r.posted_at),
    foundAfter: foundAfter(r.posted_at, r.found_at),
    badges: DISPLAY_UPGRADES.filter(([k]) => r.upgrades?.[k]).map(([, label]) => label),
    status: r.status,
    filterReason: r.filter_reason,
    myFilterReason: r.my_filter_reason,
    score: r.score,
    openedAt: r.opened_at,
    appliedAt: r.applied_at,
    skippedAt: r.skipped_at,
    proposal: r.proposal,
    amount: r.amount,
    periodDays: r.period_days,
    activity: activity[r.id] ?? null,
    badCount: Number(r.bad_count ?? 0),
    badByMe: Boolean(r.bad_by_me),
    badReasons: r.bad_reasons ?? null,
  }));
  return { jobs, total: count ?? 0, error: "" };
}
