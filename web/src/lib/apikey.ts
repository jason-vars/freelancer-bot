import "server-only";
import { createHash, randomBytes } from "node:crypto";
import { createAdminClient } from "@/lib/supabase/admin";

// Userscript API keys. Only the SHA-256 is stored; the key is shown to its owner
// once, when created. Sent by the userscript in the X-Bot-Key header.

export const hashKey = (key: string) => createHash("sha256").update(key).digest("hex");

export function newApiKey(): { key: string; hash: string; prefix: string } {
  const key = "fbb_" + randomBytes(24).toString("base64url");
  return { key, hash: hashKey(key), prefix: key.slice(0, 8) };
}

/** The approved user owning the request's API key, or null. */
export async function userFromApiKey(req: Request): Promise<string | null> {
  const key = (req.headers.get("x-bot-key") ?? "").trim();
  if (!key) return null;
  const { data } = await createAdminClient()
    .from("profiles").select("id,status").eq("api_key_hash", hashKey(key)).maybeSingle();
  return data && data.status === "approved" ? (data.id as string) : null;
}
