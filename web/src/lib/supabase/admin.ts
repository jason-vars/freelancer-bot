import "server-only";
import { createClient } from "@supabase/supabase-js";

/** Supabase client with the SECRET key: bypasses RLS. Only for server code that has
 *  already checked who is calling (userscript API key, admin actions). */
export function createAdminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) {
    throw new Error(
      `${!url ? "NEXT_PUBLIC_SUPABASE_URL" : "SUPABASE_SECRET_KEY"} is not set on the server. ` +
      "Add it in Vercel › Settings › Environment Variables (for Production), then redeploy.",
    );
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
