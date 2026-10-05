import Link from "next/link";
import { requireApproved } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { DISPLAY_UPGRADES } from "@/lib/settings/fields";
import { jobUrl } from "@/lib/jobs";
import { JobFeed } from "./JobFeed";
import type { JobRow } from "./JobList";

const PAGE_SIZE = 50;
// Worker statuses of jobs that passed every filter and the min score.
const PASSED = ["new", "alerting", "alerted", "alert_failed", "notify_skipped"];

const VIEWS = {
  new: "New",
  opened: "Opened",
  applied: "Applied",
  passed: "All",
  skipped: "Skipped",
  bad: "Marked bad",
} as const;
const ADMIN_VIEWS = { filtered: "Filtered out", all: "Everything" } as const;
type View = keyof typeof VIEWS | keyof typeof ADMIN_VIEWS;

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

export default async function JobsPage({ searchParams }: PageProps<"/jobs">) {
  const profile = await requireApproved();
  const isAdmin = profile.role === "admin";
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
  const allowed: Record<string, string> = isAdmin ? { ...VIEWS, ...ADMIN_VIEWS } : { ...VIEWS };
  const view = (one(sp.view) in allowed ? one(sp.view) : "new") as View;
  const search = one(sp.q).trim();
  const page = Math.max(1, parseInt(one(sp.page) || "1", 10) || 1);

  const supabase = await createClient();
  let q = supabase
    .from("my_jobs")
    .select("id,title,url,description,currency,budget_min,budget_max,bid_count,bid_avg,skills,posted_at,upgrades,score,status,filter_reason,opened_at,applied_at,skipped_at,proposal,amount,period_days,my_filter_reason,bad_count,bad_by_me,bad_reasons", { count: "exact" });
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
    description: r.description ?? "",
    skills: (r.skills ?? "").split(",").map((s: string) => s.trim()).filter(Boolean),
    budget: fmtBudget(r.budget_min, r.budget_max, r.currency),
    bids: r.bid_count != null ? `${r.bid_count} bids${r.bid_avg ? ` · avg ${Math.round(r.bid_avg)}` : ""}` : "",
    posted: ago(r.posted_at),
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

  const total = count ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const href = (o: Record<string, string | number>) => {
    const p = new URLSearchParams({ view, ...(search ? { q: search } : {}), ...Object.fromEntries(Object.entries(o).map(([k, v]) => [k, String(v)])) });
    if (p.get("page") === "1") p.delete("page");
    return `/jobs?${p}`;
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <form className="ml-auto flex gap-2" action="/jobs">
          <input type="hidden" name="view" value={view} />
          <input className="input w-56" name="q" defaultValue={search} placeholder="Search titles…" />
          <button className="btn">Search</button>
        </form>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {Object.entries(allowed).map(([key, label]) => (
          <Link key={key} href={href({ view: key, page: 1 })}
            className={`btn ${key === view ? "btn-primary" : ""}`}>{label}</Link>
        ))}
      </div>

      {error && <p className="mb-4 rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">Couldn&apos;t load jobs: {error.message}</p>}
      <p className="mb-3 text-sm text-muted">{total} job{total === 1 ? "" : "s"}</p>

      <JobFeed key={`${view}|${search}|${page}`} jobs={jobs} isAdmin={isAdmin} live={page === 1} userId={profile.id}
        view={view} markAll={view === "new"} />

      {pages > 1 && (
        <div className="mt-6 flex items-center justify-center gap-3 text-sm">
          {page > 1 && <Link className="btn" href={href({ page: page - 1 })}>← Newer</Link>}
          <span className="text-muted">Page {page} of {pages}</span>
          {page < pages && <Link className="btn" href={href({ page: page + 1 })}>Older →</Link>}
        </div>
      )}
    </div>
  );
}
