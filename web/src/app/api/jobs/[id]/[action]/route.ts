import { NextResponse } from "next/server";
import { getProfile } from "@/lib/auth";
import { generateForUser, markApplied, markOpened } from "@/lib/jobs";

// Job actions from the web Jobs page, as the signed-in user.
export const maxDuration = 60;

export async function POST(_req: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await params;
  const profile = await getProfile();
  if (!profile || profile.status !== "approved") {
    return NextResponse.json({ ok: false, message: "Not signed in." }, { status: 401 });
  }
  if (!/^\d+$/.test(id)) return NextResponse.json({ ok: false, message: "Bad project id." }, { status: 400 });
  const ref = { id: Number(id) };

  let r;
  // From the web page the user picked the job on purpose, so filters and the
  // "already applied" guard don't apply (force).
  if (action === "generate") r = await generateForUser(profile.id, ref, null, { force: true });
  else if (action === "open") r = await markOpened(profile.id, ref);
  else if (action === "applied") r = await markApplied(profile.id, ref, true);
  else if (action === "unapplied") r = await markApplied(profile.id, ref, false);
  else return NextResponse.json({ ok: false, message: `Unknown action '${action}'.` }, { status: 404 });
  return NextResponse.json(r.body, { status: r.status });
}
