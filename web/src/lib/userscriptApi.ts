import "server-only";
import { checkForUser, generateForUser, jobText, markApplied, markBadForUser, type JobRef, type Result } from "@/lib/jobs";
import { createAdminClient } from "@/lib/supabase/admin";
import { getEffectiveSettings, isTrue } from "@/lib/settings/store";

// The userscript's API: the same endpoints and replies as the local Python web UI
// (/jobs/options, /jobs/generate, /jobs/check, /jobs/applied, /jobs/bad, /jobs/text),
// authenticated by the user's API key. Served by the Supabase `generate` function
// (supabase/generate-src/entry.ts), so opening projects costs the web host nothing;
// the website's /api/us route serves the same thing for older userscripts.

export const NO_API_KEY =
  "Missing or invalid API key. Open your bot Settings page, create a key, then set it from the Tampermonkey menu → “Set bot API key”.";

// Panel switches the userscript can write: query name -> setting key. Auto-bid and
// Seal are only set on the Settings page, so the panel just reads them.
const PANEL_OPTIONS: Record<string, string> = { autogen: "BOT_AUTO_GENERATE" };

async function panelOptions(userId: string, q: URLSearchParams): Promise<Result> {
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
  const list = (v: string | undefined) => (v ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  return {
    status: 200,
    body: {
      ok: true, seal: isTrue(s.BOT_SEAL_BIDS), autogen: isTrue(s.BOT_AUTO_GENERATE), autobid: isTrue(s.BOT_AUTO_BID),
      // The userscript checks the client's country as soon as a project opens.
      skip_countries: list(s.BOT_SKIP_COUNTRIES), allow_countries: list(s.BOT_ALLOW_COUNTRIES),
    },
  };
}

/** One userscript request from an already-authenticated user. `action` is the last
 *  part of the path: options, generate, check, applied, bad or text. */
export async function userscriptRequest(action: string, userId: string, q: URLSearchParams): Promise<Result> {
  if (action === "options" || action === "seal") return panelOptions(userId, q);

  const idRaw = (q.get("id") ?? "").trim();
  const seo = (q.get("seo") ?? "").trim() || null;
  if (idRaw && !/^\d+$/.test(idRaw)) return { status: 400, body: { ok: false, message: "Bad project id." } };
  const ref: JobRef = { id: idRaw ? Number(idRaw) : null, seo };
  if (!ref.id && !ref.seo) return { status: 400, body: { ok: false, message: "Missing project id or seo." } };

  const country = (q.get("country") ?? "").trim() || null;
  if (action === "generate") return generateForUser(userId, ref, country, { manual: q.get("manual") === "1" });
  if (action === "check") return checkForUser(userId, ref, country);
  if (action === "applied") return markApplied(userId, ref);
  if (action === "bad") return markBadForUser(userId, ref, (q.get("reason") ?? "").trim() || "bad");
  if (action === "text") return jobText(userId, ref);
  return { status: 404, body: { ok: false, message: `Unknown action '${action}'.` } };
}
