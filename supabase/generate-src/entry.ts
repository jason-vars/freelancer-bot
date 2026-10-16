// Source of the `generate` Edge Function: the userscript's proposal request, served by
// Supabase (up to 150 s per request) instead of the website, whose host may cut a
// request off after ~10 s. The logic is the web app's own (lib/jobs.ts, proposal.ts,
// settings), bundled by web/scripts/build-generate-fn.mjs into
// supabase/functions/generate/index.ts. Edit the web code, not the bundle.
//
// GET ?seo=…&id=…&country=…&manual=1 with the X-Bot-Key header: the same request and
// reply as the website's /api/us/jobs/generate.
import { userFromApiKey } from "@/lib/apikey";
import { generateForUser, type JobRef } from "@/lib/jobs";

declare const Deno: { serve(handler: (req: Request) => Promise<Response>): void };

const json = (status: number, body: Record<string, unknown>) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

Deno.serve(async (req) => {
  try {
    const userId = await userFromApiKey(req);
    if (!userId) {
      return json(401, { ok: false, message: "Missing or invalid API key. Open your bot Settings page, create a key, then set it from the Tampermonkey menu → “Set bot API key”." });
    }
    const q = new URL(req.url).searchParams;
    const idRaw = (q.get("id") ?? "").trim();
    const seo = (q.get("seo") ?? "").trim() || null;
    if (idRaw && !/^\d+$/.test(idRaw)) return json(400, { ok: false, message: "Bad project id." });
    const ref: JobRef = { id: idRaw ? Number(idRaw) : null, seo };
    if (!ref.id && !ref.seo) return json(400, { ok: false, message: "Missing project id or seo." });
    const r = await generateForUser(userId, ref, (q.get("country") ?? "").trim() || null, { manual: q.get("manual") === "1" });
    return json(r.status, r.body);
  } catch (e) {
    return json(500, { ok: false, message: `Generate failed: ${String((e as Error).message ?? e).slice(0, 200)}` });
  }
});
