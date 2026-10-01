import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

// A fingerprint of what the Jobs list shows: the worker's last job write plus the
// shared bad marks (count catches removals, newest time catches additions). The
// Jobs page polls this and only re-renders the list when it changes.
export const dynamic = "force-dynamic";

export async function GET() {
  const supabase = await createClient();
  const [jobs, flags, latestFlag] = await Promise.all([
    supabase.from("jobs").select("synced_at").order("synced_at", { ascending: false }).limit(1).maybeSingle(),
    supabase.from("job_flags").select("job_id", { count: "exact", head: true }),
    supabase.from("job_flags").select("created_at").order("created_at", { ascending: false }).limit(1).maybeSingle(),
  ]);
  if (jobs.error) return NextResponse.json({ ok: false, message: jobs.error.message }, { status: 401 });
  const version = [jobs.data?.synced_at ?? "", flags.count ?? 0, latestFlag.data?.created_at ?? ""].join("|");
  return NextResponse.json({ ok: true, version }, { headers: { "Cache-Control": "no-store" } });
}
