"use client";

import { createBrowserClient } from "@supabase/ssr";

/** Supabase client in the browser, signed in from the session cookie (RLS applies).
 *  Used only for Realtime: pushes when shared data changes, instead of polling. */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
  );
}
