import "server-only";
import { createClient } from "@supabase/supabase-js";

/** Supabase client with the SECRET key: bypasses RLS. Only for server code that has
 *  already checked who is calling (userscript API key, admin actions). */
export function createAdminClient() {
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SECRET_KEY!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
