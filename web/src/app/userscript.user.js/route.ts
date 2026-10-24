import { USERSCRIPT_SOURCE } from "@/generated/userscript-source";

// Serves the Tampermonkey userscript pointed at THIS site: its API base, @connect
// host and auto-update URL are rewritten from the local-bot defaults
// (http://127.0.0.1:8765), so installing it once from here keeps it updated.
export function GET(req: Request) {
  const origin = new URL(req.url).origin;
  const host = new URL(req.url).hostname;
  // Proposals are written by the Supabase "generate" function (no ~10 s host limit).
  // Trimmed: a newline pasted into the host's env setting would otherwise land inside
  // a string literal below and stop the whole userscript with a SyntaxError.
  const supabase = (process.env.NEXT_PUBLIC_SUPABASE_URL ?? "").trim().replace(/\/+$/, "");
  const genUrl = supabase ? `${supabase}/functions/v1/generate` : "";
  const body = USERSCRIPT_SOURCE
    .replace(/^\/\/ @name(\s+)(.*)$/m, (_m, sp, name) => `// @name${sp}${name} (hosted)`)
    .replace(/^\/\/ @connect(\s+)127\.0\.0\.1$/m, (_m, sp) =>
      `// @connect${sp}${host}` + (supabase ? `\n// @connect${sp}${new URL(supabase).hostname}` : ""))
    .replace(/const GEN_URL = "";/, `const GEN_URL = "${genUrl}";`)
    // Runs on this site too, so job links open in a background tab.
    .replace(/^\/\/ @match(\s+)http:\/\/127\.0\.0\.1:8765\/\*$/m, (_m, sp) => `// @match${sp}${origin}/*`)
    .replace(/^\/\/ @updateURL(\s+).*$/m, (_m, sp) => `// @updateURL${sp}${origin}/userscript.user.js`)
    .replace(/^\/\/ @downloadURL(\s+).*$/m, (_m, sp) => `// @downloadURL${sp}${origin}/userscript.user.js`)
    .replace(/const BOT_BASE = "http:\/\/127\.0\.0\.1:8765";/, `const BOT_BASE = "${origin}/api/us";`)
    .replaceAll(" — is `python -m bot serve` running?", ". Check your internet connection.");
  return new Response(body, {
    headers: { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store" },
  });
}
