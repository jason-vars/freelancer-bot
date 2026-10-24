// Source of the `generate` Edge Function: the userscript's whole API, served by
// Supabase (up to 150 s per request, and no cost to the web host) instead of the
// website. The logic is the web app's own (lib/userscriptApi.ts, jobs.ts,
// proposal.ts, settings), bundled by web/scripts/build-generate-fn.mjs into
// supabase/functions/generate/index.ts. Edit the web code, not the bundle.
//
//   /generate                  the proposal (GEN_URL in the userscript)
//   /generate/jobs/<action>    options, generate, check, applied, bad, text
//
// The userscript authenticates with the X-Bot-Key header. The Jobs page's own
// "Generate proposal" button sends the signed-in user's Supabase session instead
// (Authorization: Bearer …) and may only generate.
import { userFromApiKey } from "@/lib/apikey";
import { generateForUser } from "@/lib/jobs";
import { createAdminClient } from "@/lib/supabase/admin";
import { NO_API_KEY, userscriptRequest } from "@/lib/userscriptApi";

declare const Deno: { serve(handler: (req: Request) => Promise<Response>): void };

// The Jobs page calls from the website's origin. Access is by token, never cookies,
// so any origin may ask.
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-bot-key, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

const json = (status: number, body: Record<string, unknown>) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...CORS } });

/** The approved user behind a Supabase session token, or null. */
async function userFromSession(req: Request): Promise<string | null> {
  const m = /^Bearer\s+(\S+)$/i.exec(req.headers.get("authorization") ?? "");
  if (!m) return null;
  const db = createAdminClient();
  const { data } = await db.auth.getUser(m[1]);
  if (!data.user) return null;
  const { data: p } = await db.from("profiles").select("status").eq("id", data.user.id).maybeSingle();
  return p?.status === "approved" ? data.user.id : null;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  try {
    const url = new URL(req.url);
    const action = /\/jobs\/([a-z]+)\/?$/.exec(url.pathname)?.[1] ?? "generate";

    const apiUser = req.headers.get("x-bot-key") ? await userFromApiKey(req) : null;
    if (apiUser) {
      const r = await userscriptRequest(action, apiUser, url.searchParams);
      return json(r.status, r.body);
    }

    // The website's Generate button: the user picked the job on purpose, so filters
    // and the "already applied" guard don't apply (force), as on /api/jobs/[id]/generate.
    const webUser = action === "generate" ? await userFromSession(req) : null;
    if (webUser) {
      const id = (url.searchParams.get("id") ?? "").trim();
      if (!/^\d+$/.test(id)) return json(400, { ok: false, message: "Bad project id." });
      const r = await generateForUser(webUser, { id: Number(id) }, null, { force: true });
      return json(r.status, r.body);
    }
    return json(401, { ok: false, message: NO_API_KEY });
  } catch (e) {
    return json(500, { ok: false, message: `Request failed: ${String((e as Error).message ?? e).slice(0, 200)}` });
  }
});
