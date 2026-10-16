// The Edge Function's version of web/src/lib/supabase/admin.ts: same secret-key
// client, from the variables Supabase gives every function.
import { createClient } from "@supabase/supabase-js";

declare const Deno: { env: { get(k: string): string | undefined } };

export function createAdminClient() {
  return createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
