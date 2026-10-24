"use client";

import { createBrowserClient } from "@supabase/ssr";

/** Supabase client in the browser, signed in from the session cookie (RLS applies).
 *  Used only for Realtime: pushes when shared data changes, instead of polling. */
export function createClient() {
  // Trimmed: a value pasted into the host's settings with a trailing newline or
  // space otherwise breaks every Supabase request.
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!.trim(),
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!.trim(),
  );
}
