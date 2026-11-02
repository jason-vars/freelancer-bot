import Link from "next/link";
import { requireAdmin } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";
import { deleteUser, requestFetch, setCanMarkBad, setUserRole, setUserStatus } from "./actions";

function when(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC"
    : "never";
}

// No heartbeat for 15 minutes = the worker is down (or its CLOUD_* keys are wrong).
function isStale(iso: string | null): boolean {
  return !iso || Date.now() - new Date(iso).getTime() > 15 * 60 * 1000;
}

export default async function AdminPage() {
  const me = await requireAdmin();
  const supabase = await createClient();
  const [{ data: users }, { data: worker }, { count: jobCount }] = await Promise.all([
    // "*" so the list still loads on a database that lacks a newer column.
    supabase.from("profiles").select("*")
      .order("status", { ascending: true }).order("created_at", { ascending: false }),
    supabase.from("worker_status").select("*").eq("id", 1).maybeSingle(),
    supabase.from("jobs").select("id", { count: "exact", head: true }),
  ]);
  const stale = isStale(worker?.last_cycle_at ?? null);
  const pending = (users ?? []).filter((u) => u.status === "pending").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Admin</h1>
        <Link href="/admin/settings" className="btn btn-primary ml-auto">Bot settings →</Link>
      </div>

      <section className="card p-5">
        <div className="flex flex-wrap items-start gap-4">
          <div className="flex-1">
            <h2 className="text-lg font-semibold">Job worker</h2>
            <p className="mt-1 text-sm">
              <span className={`chip ${stale ? "chip-bad" : "chip-ok"}`}>{stale ? "No recent heartbeat" : "Running"}</span>
              <span className="ml-2 text-muted">Last cycle: {when(worker?.last_cycle_at ?? null)}</span>
            </p>
            {worker?.message && <p className="mt-2 text-sm text-muted">{worker.message}</p>}
            <p className="mt-2 text-sm text-muted">
              {jobCount ?? 0} jobs stored. Fetch interval and hours are in Bot settings.
            </p>
          </div>
          <form action={requestFetch}>
            <button className="btn" disabled={Boolean(worker?.run_requested_at)}>
              {worker?.run_requested_at ? "Fetch requested…" : "Fetch now"}
            </button>
          </form>
        </div>
      </section>

      <section className="card p-5">
        <h2 className="text-lg font-semibold">
          Users {pending > 0 && <span className="chip chip-warn ml-2">{pending} waiting</span>}
        </h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted">
                <th className="py-2 pr-3 font-medium">User</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Role</th>
                <th className="py-2 pr-3 font-medium" title="Can mark jobs bad by hand. A bad job leaves every user's list.">Mark bad</th>
                <th className="py-2 pr-3 font-medium">Registered</th>
                <th className="py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {(users ?? []).map((u) => {
                const self = u.id === me.id;
                return (
                  <tr key={u.id} className="border-b border-border last:border-0">
                    <td className="py-2.5 pr-3">
                      <div className="font-medium">{u.full_name || "—"}</div>
                      <div className="text-xs text-muted">
                        {u.email}{u.api_key_prefix ? " · userscript key set" : ""}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <span className={`chip ${u.status === "approved" ? "chip-ok" : u.status === "pending" ? "chip-warn" : "chip-bad"}`}>
                        {u.status}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3">{u.role}</td>
                    <td className="py-2.5 pr-3">
                      {u.role === "admin" ? <span className="text-xs text-muted">always</span> : (
                        <form action={setCanMarkBad.bind(null, u.id, !u.can_mark_bad)} className="flex items-center gap-2">
                          <span className={`chip ${u.can_mark_bad ? "chip-ok" : ""}`}>{u.can_mark_bad ? "allowed" : "no"}</span>
                          <button className="btn">{u.can_mark_bad ? "Revoke" : "Allow"}</button>
                        </form>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-muted">{when(u.created_at)}</td>
                    <td className="py-2.5">
                      {self ? <span className="text-xs text-muted">you</span> : (
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {u.status !== "approved" && (
                            <form action={setUserStatus.bind(null, u.id, "approved")}>
                              <button className="btn btn-primary">Approve</button>
                            </form>
                          )}
                          {u.status !== "rejected" && (
                            <form action={setUserStatus.bind(null, u.id, "rejected")}>
                              <button className="btn">{u.status === "approved" ? "Revoke" : "Reject"}</button>
                            </form>
                          )}
                          {u.status === "approved" && (
                            <form action={setUserRole.bind(null, u.id, u.role === "admin" ? "user" : "admin")}>
                              <button className="btn">{u.role === "admin" ? "Remove admin" : "Make admin"}</button>
                            </form>
                          )}
                          <form action={deleteUser.bind(null, u.id)}>
                            <button className="btn btn-danger">Delete</button>
                          </form>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
