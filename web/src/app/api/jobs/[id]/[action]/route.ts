import { NextResponse } from "next/server";
import { getProfile } from "@/lib/auth";
import { generateForUser, markApplied, markOpened, markSkipped } from "@/lib/jobs";
import { createClient } from "@/lib/supabase/server";

// Job actions from the web Jobs page, as the signed-in user.
export const maxDuration = 60;

const json = (status: number, body: Record<string, unknown>) => NextResponse.json(body, { status });

/** Shared "bad job" marks. Written with the user's own session, so the job_flags
 *  policies decide: anyone approved marks/unmarks their own, admins clear all. */
async function flag(action: string, jobId: number, userId: string, req: Request) {
  const supabase = await createClient();
  if (action === "bad") {
    const body = await req.json().catch(() => ({}));
    const reason = String(body?.reason ?? "").trim().slice(0, 200);
    const { error } = await supabase.from("job_flags").upsert({ job_id: jobId, user_id: userId, reason });
    return error ? json(400, { ok: false, message: error.message }) : json(200, { ok: true });
  }
  const q = supabase.from("job_flags").delete().eq("job_id", jobId);
  const { error } = await (action === "unbad" ? q.eq("user_id", userId) : q);
  return error ? json(400, { ok: false, message: error.message }) : json(200, { ok: true });
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await params;
  const profile = await getProfile();
  if (!profile || profile.status !== "approved") return json(401, { ok: false, message: "Not signed in." });
  if (!/^\d+$/.test(id)) return json(400, { ok: false, message: "Bad project id." });
  const ref = { id: Number(id) };

  if (action === "bad" || action === "unbad") return flag(action, ref.id, profile.id, req);
  if (action === "clearbad") {
    if (profile.role !== "admin") return json(403, { ok: false, message: "Admins only." });
    return flag(action, ref.id, profile.id, req);
  }

  let r;
  // From the web page the user picked the job on purpose, so filters and the
  // "already applied" guard don't apply (force).
  if (action === "generate") r = await generateForUser(profile.id, ref, null, { force: true });
  else if (action === "open") r = await markOpened(profile.id, ref);
  else if (action === "applied") r = await markApplied(profile.id, ref, true);
  else if (action === "unapplied") r = await markApplied(profile.id, ref, false);
  else if (action === "skip") r = await markSkipped(profile.id, [ref.id], true);
  else if (action === "unskip") r = await markSkipped(profile.id, [ref.id], false);
  else return json(404, { ok: false, message: `Unknown action '${action}'.` });
  return json(r.status, r.body);
}
