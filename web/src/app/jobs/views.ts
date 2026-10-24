// The Jobs page's tabs, shared by the page (tabs, links) and the browser-side list
// loader (jobsQuery.ts). No imports, so both sides can use it.

export const PAGE_SIZE = 50;
// Worker statuses of jobs that passed every filter and the min score.
export const PASSED = ["new", "alerting", "alerted", "alert_failed", "notify_skipped"];

export const VIEWS = {
  new: "New",
  opened: "Opened",
  applied: "Applied",
  passed: "All",
  skipped: "Skipped",
  bad: "Marked bad",
} as const;
export const ADMIN_VIEWS = { filtered: "Filtered out", all: "Everything" } as const;
export type View = keyof typeof VIEWS | keyof typeof ADMIN_VIEWS;

/** Link to a Jobs tab / search / page; `o` overrides the current ones. */
export function jobsHref(view: string, search: string, o: Record<string, string | number> = {}): string {
  const p = new URLSearchParams({ view, ...(search ? { q: search } : {}), ...Object.fromEntries(Object.entries(o).map(([k, v]) => [k, String(v)])) });
  if (p.get("page") === "1") p.delete("page");
  return `/jobs?${p}`;
}
