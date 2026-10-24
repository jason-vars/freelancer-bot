"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { JobList, type JobRow, type JobState } from "./JobList";
import { loadJobs } from "./jobsQuery";
import { PAGE_SIZE, jobsHref, type View } from "./views";

// Live job list. It loads the tab straight from Supabase in the browser and reloads
// it there when Realtime reports a change, so new jobs show within a second of the
// fetcher saving them and cost the web host nothing. Jobs that weren't there before
// are highlighted and counted in a banner and the tab title (plus an optional
// desktop alert). The page keys this component by view/search/page, so switching
// views remounts it instead of counting the new view's jobs as "new".

// Backup only, in case the Realtime socket drops: Realtime brings changes at once.
const POLL_MS = 300_000;
// Worker statuses of jobs that show in the lists (views.ts PASSED). Realtime only
// wakes the page for NEW jobs in one of these, not for filtered ones or status edits.
const LISTED = "new,alerting,alerted,alert_failed,notify_skipped";
const ALERTS_KEY = "fbb:desktopAlerts";

function beep() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
  } catch {
    // no audio (autoplay policy) — the notification itself still shows
  }
}

function notify(title: string, body: string, tag: string) {
  try {
    const note = new Notification(title, { body, tag });
    note.onclick = () => { window.focus(); note.close(); };
  } catch {
    // some browsers only allow notifications from a service worker
  }
}

/** Whether a job in this state still belongs in the tab. Mirrors the tab queries in
 *  jobsQuery.ts, so a status change moves the card right away instead of after a reload. */
function belongsIn(view: string, s: JobState): boolean {
  switch (view) {
    case "new": return !s.opened && !s.applied && !s.skipped && !s.bad;
    case "opened": return s.opened && !s.applied && !s.skipped && !s.bad;
    case "applied": return s.applied;
    case "passed": return !s.bad && !s.myFilter;
    case "skipped": return !s.applied && !s.bad && (s.skipped || s.myFilter);
    case "bad": return s.bad;
    default: return true; // admin views
  }
}

export function JobFeed({ isAdmin, userId, view, search, page }: {
  isAdmin: boolean; userId: string; view: View; search: string; page: number;
}) {
  // Only the first page follows new jobs; older pages are a fixed slice.
  const live = page === 1;
  const markAll = view === "new";

  // The tab's jobs as last loaded (null until the first load) and its total count.
  const [loaded, setLoaded] = useState<JobRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [fresh, setFresh] = useState<Set<number>>(() => new Set());
  const [latest, setLatest] = useState<JobRow[]>([]);
  // Ids shown so far; null until the first load, whose jobs are not "new".
  const known = useRef<Set<number> | null>(null);
  // Newest load wins: a slow reply never replaces a newer list.
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    const r = await loadJobs(view, search, page, isAdmin);
    if (mine !== seq.current) return;
    setError(r.error);
    if (r.error) { setLoaded((cur) => cur ?? []); return; } // keep the last good list
    setLoaded(r.jobs);
    setTotal(r.total);
    if (!known.current) { known.current = new Set(r.jobs.map((j) => j.id)); return; }
    const seen = known.current;
    const added = r.jobs.filter((j) => !seen.has(j.id));
    if (added.length) {
      added.forEach((j) => seen.add(j.id));
      setFresh((f) => new Set([...f, ...added.map((j) => j.id)]));
      setLatest(added);
    }
  }, [view, search, page, isAdmin]);
  const reloadRef = useRef(reload);
  useEffect(() => { reloadRef.current = reload; });

  useEffect(() => { void reloadRef.current(); }, []);

  const rows = loaded ?? [];
  // Jobs whose status changed here so they no longer belong in this tab: hidden at
  // once, before the reload drops them.
  const [gone, setGone] = useState<Set<number>>(() => new Set());
  // Expanded cards stay on screen even when a reload drops them (e.g. opening one
  // in "New for me"), so a proposal being read or edited never vanishes.
  const [pinned, setPinned] = useState<Map<number, JobRow>>(() => new Map());
  const jobs = [...[...pinned.values()].filter((p) => !rows.some((j) => j.id === p.id)), ...rows]
    .filter((j) => pinned.has(j.id) || !gone.has(j.id));
  const onState = (id: number, s: JobState) => setGone((g) => {
    const next = new Set(g);
    if (belongsIn(view, s)) next.delete(id); else next.add(id);
    return next;
  });
  const onExpand = (job: JobRow, open: boolean) => setPinned((m) => {
    const next = new Map(m);
    if (open) next.set(job.id, job); else next.delete(job.id);
    return next;
  });
  // Bad marks made on this page, newest last: Ctrl+Z takes them back one at a time.
  // The toast shows the latest for a few seconds with an Undo button.
  const [badUndo, setBadUndo] = useState<JobRow[]>([]);
  const [toast, setToast] = useState<JobRow | null>(null);
  const onBad = (job: JobRow) => {
    setBadUndo((u) => [...u.filter((j) => j.id !== job.id), job]);
    setToast(job);
  };
  const undoBad = async () => {
    const job = badUndo[badUndo.length - 1];
    if (!job) return;
    setBadUndo((u) => u.filter((j) => j.id !== job.id));
    setToast(null);
    try {
      const r = await fetch(`/api/jobs/${job.id}/unbad`, { method: "POST" });
      const d = await r.json().catch(() => ({ ok: false, message: `HTTP ${r.status}` }));
      if (!d.ok) throw new Error(d.message ?? "Couldn't undo.");
    } catch (e) {
      setBadUndo((u) => [...u, job]);
      return alert(`Couldn't undo the bad mark: ${(e as Error).message}`);
    }
    // Back in the list: the reload brings it with its mark gone.
    setGone((g) => { const next = new Set(g); next.delete(job.id); return next; });
    void reload();
  };
  // Ctrl+Z / Cmd+Z, except while typing (a text box keeps its own undo).
  const undoRef = useRef(undoBad);
  useEffect(() => { undoRef.current = undoBad; });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== "z") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      void undoRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(t);
  }, [toast]);

  const [marking, setMarking] = useState(false);
  const [alerts, setAlerts] = useState(false);

  // Only new jobs still on screen count: one that left the list since (opened via
  // the userscript, hidden by a filter) must not keep inflating the number.
  const unread = jobs.filter((j) => fresh.has(j.id)).length;

  // Backup poll, visible tabs only (Supabase, not the web host).
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => { if (!document.hidden) void reloadRef.current(); }, POLL_MS);
    return () => clearInterval(timer);
  }, [live]);

  // Jobs announced straight from a Realtime message (background tab), so the reload
  // that later shows them doesn't alert a second time.
  const notified = useRef(new Set<number>());
  const alertsRef = useRef(alerts);
  useEffect(() => { alertsRef.current = alerts; }, [alerts]);

  // New listed jobs, shared bad marks and this user's own opened/applied changes (also
  // from the userscript) reach this page at once. A burst is folded into one reload.
  // A background tab doesn't reload: it alerts from the message itself and reloads
  // once you come back to it.
  useEffect(() => {
    if (!live) return;
    const supabase = createClient();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pending = false;
    const changed = () => {
      if (document.hidden) { pending = true; return; }
      clearTimeout(timer);
      timer = setTimeout(() => void reloadRef.current(), 300);
    };
    const jobAdded = (payload: { new: Record<string, unknown> }) => {
      const j = payload.new;
      if (document.hidden && alertsRef.current && "Notification" in window && Notification.permission === "granted") {
        const id = Number(j.id);
        if (!notified.current.has(id)) {
          notified.current.add(id);
          const n = (x: unknown) => (x == null ? "" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 }));
          const budget = j.budget_min == null && j.budget_max == null ? "" : ` (${n(j.budget_min)}–${n(j.budget_max)} ${j.currency ?? ""})`;
          notify("New job", `• ${String(j.title ?? "")}${budget}`, `fbb-new-jobs-${id}`);
          beep();
        }
      }
      changed();
    };
    const onVisible = () => {
      if (!document.hidden && pending) { pending = false; changed(); }
    };
    document.addEventListener("visibilitychange", onVisible);
    const channel = supabase
      .channel("jobs_feed")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "jobs", filter: `status=in.(${LISTED})` }, jobAdded)
      .on("postgres_changes", { event: "*", schema: "public", table: "job_flags" }, changed)
      .on("postgres_changes", { event: "*", schema: "public", table: "user_jobs", filter: `user_id=eq.${userId}` }, changed)
      .subscribe();
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      void supabase.removeChannel(channel);
    };
  }, [live, userId]);

  // Desktop alerts preference (per browser).
  useEffect(() => {
    try {
      const on = localStorage.getItem(ALERTS_KEY) === "1" && "Notification" in window && Notification.permission === "granted";
      if (on) queueMicrotask(() => setAlerts(true));
    } catch { /* storage blocked */ }
  }, []);

  // Count in the tab title while there are unseen jobs.
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\)\s*/, "");
    document.title = unread ? `(${unread}) ${base}` : base;
  }, [unread]);

  // Desktop notification + sound for each batch, when enabled.
  useEffect(() => {
    if (!latest.length || !alerts || !("Notification" in window) || Notification.permission !== "granted") return;
    // Skip jobs a background tab already announced from the Realtime message.
    const todo = latest.filter((j) => !notified.current.has(j.id));
    todo.forEach((j) => notified.current.add(j.id));
    if (!todo.length) return;
    const n = todo.length;
    // A tag per batch: macOS keeps notifications in Notification Center, and one
    // with a reused tag silently replaces the old one there instead of popping up.
    notify(`${n} new job${n === 1 ? "" : "s"}`, todo.slice(0, 3).map((j) => `• ${j.title} (${j.budget})`).join("\n"),
      `fbb-new-jobs-${todo[0].id}`);
    beep();
  }, [latest, alerts]);

  const toggleAlerts = async () => {
    if (alerts) {
      setAlerts(false);
      try { localStorage.setItem(ALERTS_KEY, "0"); } catch {}
      return;
    }
    if (!("Notification" in window)) return alert("This browser doesn't support desktop notifications.");
    const perm = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
    if (perm !== "granted") return alert("Notifications are blocked for this site. Allow them in the browser's site settings.");
    setAlerts(true);
    try { localStorage.setItem(ALERTS_KEY, "1"); } catch {}
    // Shows at once whether alerts get through: the browser can allow them while the
    // OS (macOS System Settings › Notifications) still hides them.
    notify("Desktop alerts are on", "You'll get a notification like this for new jobs. No pop-up? Allow your browser in your system's notification settings.", "fbb-test");
    beep();
  };

  const dismiss = () => {
    setFresh(new Set());
  };

  // Clears the New list: marks every job on screen skipped. Only the ids shown are
  // sent, so a job that arrives while the dialog is open isn't swept up.
  const markAllSkipped = async () => {
    const ids = jobs.map((j) => j.id);
    if (!ids.length || !confirm(`Mark all ${ids.length} job${ids.length === 1 ? "" : "s"} on this page as skipped?`)) return;
    setMarking(true);
    try {
      const r = await fetch("/api/jobs/skipped", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      const d = await r.json().catch(() => ({ ok: false, message: `HTTP ${r.status}` }));
      if (!d.ok) alert(d.message ?? "Couldn't update.");
      else {
        setGone((g) => new Set([...g, ...ids]));
        dismiss();
        void reload();
      }
    } catch {
      alert("Couldn't reach the server.");
    } finally {
      setMarking(false);
    }
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      {error && <p className="mb-4 rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">Couldn&apos;t load jobs: {error}</p>}
      <p className="mb-3 text-sm text-muted">{loaded === null ? "Loading…" : `${total} job${total === 1 ? "" : "s"}`}</p>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {live && <span className="text-xs text-muted">Updates automatically</span>}
        {markAll && jobs.length > 0 && (
          <button className="btn ml-auto" onClick={markAllSkipped} disabled={marking}>
            {marking ? "Marking…" : "Mark all as skipped"}
          </button>
        )}
        <button className={`btn ${markAll && jobs.length > 0 ? "" : "ml-auto"}`} onClick={toggleAlerts}>
          {alerts ? "🔔 Desktop alerts: on" : "🔕 Desktop alerts: off"}
        </button>
      </div>
      {unread > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex items-center gap-3 rounded-lg bg-accent px-4 py-2 text-sm text-accent-fg shadow-lg">
          <span className="flex items-center gap-2 font-semibold">
            <span className="rounded-full bg-accent-fg px-2 py-0.5 text-xs font-bold text-accent">{unread}</span>
            new job{unread === 1 ? "" : "s"}
          </span>
          <button className="ml-auto underline" onClick={() => { window.scrollTo({ top: 0, behavior: "smooth" }); dismiss(); }}>
            Mark seen
          </button>
        </div>
      )}
      {loaded === null
        ? <div className="card p-8 text-center text-sm text-muted">Loading jobs…</div>
        : <JobList jobs={jobs} isAdmin={isAdmin} fresh={fresh} onState={onState} onExpand={onExpand} onBad={onBad}
            onChanged={() => void reload()} />}

      {pages > 1 && (
        <div className="mt-6 flex items-center justify-center gap-3 text-sm">
          {page > 1 && <Link className="btn" href={jobsHref(view, search, { page: page - 1 })}>← Newer</Link>}
          <span className="text-muted">Page {page} of {pages}</span>
          {page < pages && <Link className="btn" href={jobsHref(view, search, { page: page + 1 })}>Older →</Link>}
        </div>
      )}
      {toast && (
        <div className="fixed inset-x-4 bottom-4 z-20 mx-auto flex max-w-lg items-center gap-3 rounded-lg border border-border bg-surface px-4 py-2 text-sm shadow-lg">
          <span className="min-w-0 flex-1 truncate">👎 Marked bad: {toast.title}</span>
          <button className="btn" onClick={() => void undoBad()}>Undo</button>
          <span className="hidden text-xs text-muted sm:inline">Ctrl+Z</span>
        </div>
      )}
    </div>
  );
}
