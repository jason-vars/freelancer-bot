"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { JobList, type JobRow, type JobState } from "./JobList";

// Live job list: polls /api/jobs/version and re-renders the page in place when the
// worker stored something, then highlights the jobs that weren't there before and
// counts them in a banner and the tab title (plus an optional desktop alert).
// The page keys this component by view/search/page, so switching views remounts it
// instead of counting the new view's jobs as "new".

const POLL_MS = 20_000;
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
 *  page.tsx, so a status change moves the card right away instead of after a refresh. */
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

export function JobFeed({ jobs: serverJobs, isAdmin, live, userId, view, markAll = false }: {
  jobs: JobRow[]; isAdmin: boolean; live: boolean; userId: string; view: string; markAll?: boolean;
}) {
  const router = useRouter();
  // Jobs whose status changed here so they no longer belong in this tab: hidden at
  // once, before the server refresh drops them.
  const [gone, setGone] = useState<Set<number>>(() => new Set());
  // Expanded cards stay on screen even when a refresh drops them (e.g. opening one
  // in "New for me"), so a proposal being read or edited never vanishes.
  const [pinned, setPinned] = useState<Map<number, JobRow>>(() => new Map());
  const jobs = [...[...pinned.values()].filter((p) => !serverJobs.some((j) => j.id === p.id)), ...serverJobs]
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
  const [marking, setMarking] = useState(false);
  const [prevJobs, setPrevJobs] = useState(serverJobs);
  const [known, setKnown] = useState(() => new Set(serverJobs.map((j) => j.id)));
  const [fresh, setFresh] = useState<Set<number>>(() => new Set());
  const [latest, setLatest] = useState<JobRow[]>([]);
  const [alerts, setAlerts] = useState(false);

  // A refresh brought a new list: anything we haven't shown before is new.
  if (serverJobs !== prevJobs) {
    setPrevJobs(serverJobs);
    const added = serverJobs.filter((j) => !known.has(j.id));
    if (added.length) {
      setKnown(new Set([...known, ...added.map((j) => j.id)]));
      setFresh(new Set([...fresh, ...added.map((j) => j.id)]));
      setLatest(added);
    }
  }

  // Only new jobs still on screen count: one that left the list since (opened via
  // the userscript, hidden by a filter) must not keep inflating the number.
  const unread = jobs.filter((j) => fresh.has(j.id)).length;

  // Poll for worker writes; refresh only when something changed.
  useEffect(() => {
    if (!live) return;
    let version: string | null | undefined;
    let stopped = false;
    const check = async () => {
      try {
        const r = await fetch("/api/jobs/version", { cache: "no-store" });
        const d = await r.json();
        if (stopped || !d.ok) return;
        if (version !== undefined && d.version !== version) router.refresh();
        version = d.version;
      } catch {
        // offline for a moment — try again next tick
      }
    };
    void check();
    const timer = setInterval(() => { if (!document.hidden || alerts) void check(); }, POLL_MS);
    const onVisible = () => { if (!document.hidden) void check(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [live, alerts, router]);

  // New jobs, shared bad marks and this user's own opened/applied changes (also from
  // the userscript) should reach this page at once, not at the next poll. Realtime
  // pushes every jobs / job_flags / own user_jobs change (RLS still applies); a burst
  // of changes (one worker sync writes many rows) is folded into one refresh. The
  // poll above stays as the fallback if the socket drops.
  useEffect(() => {
    if (!live) return;
    const supabase = createClient();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const changed = () => {
      clearTimeout(timer);
      timer = setTimeout(() => router.refresh(), 300);
    };
    const channel = supabase
      .channel("jobs_feed")
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs" }, changed)
      .on("postgres_changes", { event: "*", schema: "public", table: "job_flags" }, changed)
      .on("postgres_changes", { event: "*", schema: "public", table: "user_jobs", filter: `user_id=eq.${userId}` }, changed)
      .subscribe();
    return () => {
      clearTimeout(timer);
      void supabase.removeChannel(channel);
    };
  }, [live, router, userId]);

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
    const n = latest.length;
    // A tag per batch: macOS keeps notifications in Notification Center, and one
    // with a reused tag silently replaces the old one there instead of popping up.
    notify(`${n} new job${n === 1 ? "" : "s"}`, latest.slice(0, 3).map((j) => `• ${j.title} (${j.budget})`).join("\n"),
      `fbb-new-jobs-${latest[0].id}`);
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
        router.refresh();
      }
    } catch {
      alert("Couldn't reach the server.");
    } finally {
      setMarking(false);
    }
  };

  return (
    <div>
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
      <JobList jobs={jobs} isAdmin={isAdmin} fresh={fresh} onState={onState} onExpand={onExpand} />
    </div>
  );
}
