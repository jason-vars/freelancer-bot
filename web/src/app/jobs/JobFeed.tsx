"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { JobList, type JobRow } from "./JobList";

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

export function JobFeed({ jobs, isAdmin, live }: { jobs: JobRow[]; isAdmin: boolean; live: boolean }) {
  const router = useRouter();
  const [prevJobs, setPrevJobs] = useState(jobs);
  const [known, setKnown] = useState(() => new Set(jobs.map((j) => j.id)));
  const [fresh, setFresh] = useState<Set<number>>(() => new Set());
  const [latest, setLatest] = useState<JobRow[]>([]);
  const [alerts, setAlerts] = useState(false);

  // A refresh brought a new list: anything we haven't shown before is new.
  if (jobs !== prevJobs) {
    setPrevJobs(jobs);
    const added = jobs.filter((j) => !known.has(j.id));
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

  // Bad marks are shared, so another user's mark should reach this page at once, not
  // at the next poll. Realtime pushes every job_flags change (RLS still applies);
  // a burst of changes is folded into one refresh. The poll above stays as the
  // fallback if the socket drops.
  useEffect(() => {
    if (!live) return;
    const supabase = createClient();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const channel = supabase
      .channel("job_flags")
      .on("postgres_changes", { event: "*", schema: "public", table: "job_flags" }, () => {
        clearTimeout(timer);
        timer = setTimeout(() => router.refresh(), 300);
      })
      .subscribe();
    return () => {
      clearTimeout(timer);
      void supabase.removeChannel(channel);
    };
  }, [live, router]);

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
    const note = new Notification(`${n} new job${n === 1 ? "" : "s"}`, {
      body: latest.slice(0, 3).map((j) => `• ${j.title} (${j.budget})`).join("\n"),
      tag: "fbb-new-jobs",
    });
    note.onclick = () => { window.focus(); note.close(); };
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
  };

  const dismiss = () => {
    setFresh(new Set());
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {live && <span className="text-xs text-muted">Updates automatically</span>}
        <button className="btn ml-auto" onClick={toggleAlerts}>
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
      <JobList jobs={jobs} isAdmin={isAdmin} fresh={fresh} />
    </div>
  );
}
