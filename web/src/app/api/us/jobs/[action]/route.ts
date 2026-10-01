import { NextResponse } from "next/server";
import { userFromApiKey } from "@/lib/apikey";
import { generateForUser, jobText, markApplied, type JobRef } from "@/lib/jobs";
import { createAdminClient } from "@/lib/supabase/admin";
import { getEffectiveSettings, isTrue } from "@/lib/settings/store";

// The userscript's API: the same four endpoints and replies as the local Python
// web UI (/jobs/options, /jobs/generate, /jobs/applied, /jobs/text), authenticated
// by the user's API key instead of being bound to 127.0.0.1.

export const maxDuration = 60; // one OpenAI call for the proposal, plus pricing

const json = (status: number, body: Record<string, unknown>) => NextResponse.json(body, { status });

// Panel switches the userscript can read and write: query name -> setting key.
const PANEL_OPTIONS: Record<string, string> = { seal: "BOT_SEAL_BIDS", autogen: "BOT_AUTO_GENERATE" };

async function panelOptions(userId: string, q: URLSearchParams) {
  const updates: Record<string, string> = {};
  for (const [name, key] of Object.entries(PANEL_OPTIONS)) {
    const raw = q.get(name);
    if (raw !== null && raw.trim()) updates[key] = isTrue(raw) ? "1" : "0";
  }
  if (Object.keys(updates).length) {
    const db = createAdminClient();
    const { data } = await db.from("user_settings").select("values").eq("user_id", userId).maybeSingle();
    await db.from("user_settings").upsert({
      user_id: userId, values: { ...((data?.values as object) ?? {}), ...updates }, updated_at: new Date().toISOString(),
    });
  }
  const s = await getEffectiveSettings(userId);
  return json(200, { ok: true, seal: isTrue(s.BOT_SEAL_BIDS), autogen: isTrue(s.BOT_AUTO_GENERATE) });
}

export async function GET(req: Request, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  const userId = await userFromApiKey(req);
  if (!userId) {
    return json(401, { ok: false, message: "Missing or invalid API key. Open your bot Settings page, create a key, then set it from the Tampermonkey menu → “Set bot API key”." });
  }
  const q = new URL(req.url).searchParams;
  if (action === "options" || action === "seal") return panelOptions(userId, q);

  const idRaw = (q.get("id") ?? "").trim();
  const seo = (q.get("seo") ?? "").trim() || null;
  if (idRaw && !/^\d+$/.test(idRaw)) return json(400, { ok: false, message: "Bad project id." });
  const ref: JobRef = { id: idRaw ? Number(idRaw) : null, seo };
  if (!ref.id && !ref.seo) return json(400, { ok: false, message: "Missing project id or seo." });

  let r;
  if (action === "generate") {
    r = await generateForUser(userId, ref, (q.get("country") ?? "").trim() || null, { manual: q.get("manual") === "1" });
  }
  else if (action === "applied") r = await markApplied(userId, ref);
  else if (action === "text") r = await jobText(userId, ref);
  else return json(404, { ok: false, message: `Unknown action '${action}'.` });
  return json(r.status, r.body);
}
