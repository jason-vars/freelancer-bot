"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export type JobRow = {
  id: number;
  title: string;
  url: string;
  description: string;
  skills: string[];
  budget: string;
  bids: string;
  posted: string;
  badges: string[];
  status: string;
  filterReason: string | null;
  myFilterReason: string | null;
  score: number;
  openedAt: string | null;
  appliedAt: string | null;
  proposal: string | null;
  amount: number | null;
  periodDays: number | null;
  activity: { opened: number; applied: number } | null;
  badCount: number;
  badByMe: boolean;
  badReasons: string | null;
};

async function post(id: number, action: string, body?: Record<string, unknown>) {
  const r = await fetch(`/api/jobs/${id}/${action}`, {
    method: "POST",
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const data = await r.json().catch(() => ({ ok: false, message: `HTTP ${r.status}` }));
  return data as { ok: boolean; message?: string; proposal?: string; amount?: number; period?: number; currency?: string };
}

export function JobList({ jobs, isAdmin, fresh }: { jobs: JobRow[]; isAdmin: boolean; fresh?: Set<number> }) {
  if (!jobs.length) return <div className="card p-8 text-center text-sm text-muted">No jobs here yet.</div>;
  return (
    <ul className="space-y-3">
      {jobs.map((j) => <Job key={j.id} job={j} isAdmin={isAdmin} fresh={fresh?.has(j.id) ?? false} />)}
    </ul>
  );
}

function Job({ job, isAdmin, fresh }: { job: JobRow; isAdmin: boolean; fresh: boolean }) {
  const router = useRouter();
  const [opened, setOpened] = useState(Boolean(job.openedAt));
  const [applied, setApplied] = useState(Boolean(job.appliedAt));
  const [expanded, setExpanded] = useState(false);
  const [proposal, setProposal] = useState(job.proposal ?? "");
  const [pricing, setPricing] = useState(
    job.amount != null ? `${job.amount} · ${job.periodDays ?? "?"} days` : "",
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  // Bad mark shown from local state so a click shows at once (the server refresh
  // follows in the background). A refresh bringing new marks — yours confirmed, or
  // another user's via the live feed — replaces it.
  const fromProps = { count: job.badCount, byMe: job.badByMe, reasons: job.badReasons };
  const [bad, setBadState] = useState(fromProps);
  const [badProps, setBadProps] = useState(fromProps);
  if (badProps.count !== fromProps.count || badProps.byMe !== fromProps.byMe || badProps.reasons !== fromProps.reasons) {
    setBadProps(fromProps);
    setBadState(fromProps);
  }

  const markOpened = () => {
    if (!opened) {
      setOpened(true);
      void post(job.id, "open");
    }
  };

  const generate = async () => {
    setBusy("generate");
    setError("");
    const d = await post(job.id, "generate");
    setBusy(null);
    if (!d.ok) return setError(d.message ?? "Generation failed.");
    setProposal(d.proposal ?? "");
    setPricing(`${d.amount} ${d.currency ?? ""} · ${d.period} days`);
    setOpened(true);
  };

  const toggleApplied = async () => {
    setBusy("applied");
    const d = await post(job.id, applied ? "unapplied" : "applied");
    setBusy(null);
    if (d.ok) {
      setApplied(!applied);
      setOpened(true);
    } else setError(d.message ?? "Couldn't update.");
  };

  // Shared with everyone: a job marked bad leaves all users' New/All matching lists.
  // One click marks it: no reason prompt (Undo bad mark reverses a slip).
  const setBad = async (action: "bad" | "unbad" | "clearbad") => {
    if (action === "clearbad" && !confirm("Remove every user's bad mark from this job?")) return;
    // Show the result immediately; undo it if the server says no.
    const before = bad;
    if (action === "bad") {
      setBadState({ count: before.count + (before.byMe ? 0 : 1), byMe: true, reasons: before.reasons });
    } else if (action === "unbad") {
      setBadState({ count: Math.max(0, before.count - 1), byMe: false, reasons: before.count > 1 ? before.reasons : null });
    } else {
      setBadState({ count: 0, byMe: false, reasons: null });
    }
    setError("");
    setBusy("bad");
    const d = await post(job.id, action);
    setBusy(null);
    if (d.ok) router.refresh(); // moves it in/out of the New list and syncs exact reasons
    else {
      setBadState(before);
      setError(d.message ?? "Couldn't update.");
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(proposal);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const filtered = job.status === "filtered" || job.status === "skipped";

  return (
    <li className={`card p-4 transition-shadow ${fresh ? "ring-2 ring-accent" : ""} ${opened && !expanded && !fresh ? "opacity-75" : ""}`}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <a href={job.url} target="_blank" rel="noreferrer" onClick={markOpened} data-fbb-bg-open
          className="min-w-0 flex-1 font-medium hover:text-accent">
          {job.title}
        </a>
        <span className="text-sm font-medium whitespace-nowrap">{job.budget}</span>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
        <span>{job.posted}</span>
        {job.bids && <span>· {job.bids}</span>}
        {job.score > 0 && <span>· score {job.score}</span>}
        {fresh && <span className="chip chip-new">Just in</span>}
        {applied ? <span className="chip chip-ok">Applied</span>
          : opened ? <span className="chip chip-warn">Opened</span>
          : <span className="chip">New</span>}
        {job.badges.map((b) => <span key={b} className="chip">{b}</span>)}
        {bad.count > 0 && (
          <span className="chip chip-bad">
            👎 Marked bad{bad.count > 1 ? ` by ${bad.count}` : ""}{bad.byMe ? " (incl. you)" : ""}
            {bad.reasons ? `: ${bad.reasons}` : ""}
          </span>
        )}
        {job.myFilterReason && <span className="chip chip-warn">Hidden by my filter: {job.myFilterReason}</span>}
        {filtered && <span className="chip chip-bad">{job.status}{job.filterReason ? `: ${job.filterReason}` : ""}</span>}
        {isAdmin && job.activity && (
          <span className="chip">{job.activity.opened} opened · {job.activity.applied} applied</span>
        )}
      </div>

      {job.skills.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {job.skills.slice(0, 8).map((s) => <span key={s} className="chip">{s}</span>)}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn" onClick={() => { setExpanded(!expanded); markOpened(); }}>
          {expanded ? "Hide" : "Details & proposal"}
        </button>
        <a className="btn" href={job.url} target="_blank" rel="noreferrer" onClick={markOpened}>Open on Freelancer ↗</a>
        <button className="btn" onClick={toggleApplied} disabled={busy !== null}>
          {applied ? "Unmark applied" : "Mark applied"}
        </button>
        {bad.byMe ? (
          <button className="btn" onClick={() => setBad("unbad")} disabled={busy !== null}>Undo bad mark</button>
        ) : (
          <button className="btn btn-danger" onClick={() => setBad("bad")} disabled={busy !== null}>👎 Mark bad</button>
        )}
        {isAdmin && bad.count > 0 && (
          <button className="btn btn-danger" onClick={() => setBad("clearbad")} disabled={busy !== null}>Clear bad marks</button>
        )}
      </div>
      {error && !expanded && <p className="mt-2 rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">{error}</p>}

      {expanded && (
        <div className="mt-4 space-y-4 border-t border-border pt-4">
          <p className="max-h-72 overflow-auto text-sm whitespace-pre-wrap text-muted">{job.description || "(no description)"}</p>
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" onClick={generate} disabled={busy !== null}>
                {busy === "generate" ? "Writing…" : proposal ? "Regenerate proposal" : "Generate proposal"}
              </button>
              {proposal && <button className="btn" onClick={copy}>{copied ? "Copied" : "Copy"}</button>}
              {pricing && <span className="text-sm text-muted">Suggested bid: {pricing}</span>}
            </div>
            {error && <p className="mb-2 rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">{error}</p>}
            {proposal && (
              <textarea className="input min-h-56 font-mono text-[13px]" value={proposal}
                onChange={(e) => setProposal(e.target.value)} />
            )}
          </div>
        </div>
      )}
    </li>
  );
}
