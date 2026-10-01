import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

// Landing page of the sign-up confirmation email: trade the one-time code for a session.
export async function GET(req: Request) {
  const url = new URL(req.url);
  const code = url.searchParams.get("code");
  if (code) {
    const supabase = await createClient();
    await supabase.auth.exchangeCodeForSession(code);
  }
  return NextResponse.redirect(new URL("/jobs", url.origin));
}
