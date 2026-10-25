import Link from "next/link";
import { requireApproved } from "@/lib/auth";
import { JobFeed } from "./JobFeed";
import { ADMIN_VIEWS, VIEWS, jobsHref, type View } from "./views";

// Only the frame is rendered here (sign-in check, tabs, search). JobFeed loads the
// list itself from Supabase in the browser and keeps it live, so new jobs and
// refreshes cost the web host nothing.
export default async function JobsPage({ searchParams }: PageProps<"/jobs">) {
  const profile = await requireApproved();
  const isAdmin = profile.role === "admin";
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
  const allowed: Record<string, string> = isAdmin ? { ...VIEWS, ...ADMIN_VIEWS } : { ...VIEWS };
  const view = (one(sp.view) in allowed ? one(sp.view) : "new") as View;
  const search = one(sp.q).trim();
  const page = Math.max(1, parseInt(one(sp.page) || "1", 10) || 1);

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
          <Link key={key} href={jobsHref(view, search, { view: key, page: 1 })}
            className={`btn ${key === view ? "btn-primary" : ""}`}>{label}</Link>
        ))}
      </div>

      <JobFeed key={`${view}|${search}|${page}`} isAdmin={isAdmin} userId={profile.id}
        view={view} search={search} page={page} />
    </div>
  );
}
