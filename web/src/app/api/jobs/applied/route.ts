import { NextResponse } from "next/server";
import { getProfile } from "@/lib/auth";
import { markAppliedMany } from "@/lib/jobs";

// Bulk "Mark all as applied" from the Jobs page: body { ids: number[] }.
const MAX_IDS = 500;

const json = (status: number, body: Record<string, unknown>) => NextResponse.json(body, { status });

export async function POST(req: Request) {
  const profile = await getProfile();
  if (!profile || profile.status !== "approved") return json(401, { ok: false, message: "Not signed in." });
  const body = await req.json().catch(() => ({}));
  const ids = Array.isArray(body?.ids) ? body.ids.filter((x: unknown) => Number.isSafeInteger(x) && (x as number) > 0) : [];
  if (!ids.length) return json(400, { ok: false, message: "No jobs given." });
  if (ids.length > MAX_IDS) return json(400, { ok: false, message: `At most ${MAX_IDS} jobs at once.` });
  const r = await markAppliedMany(profile.id, ids);
  return json(r.status, r.body);
}
