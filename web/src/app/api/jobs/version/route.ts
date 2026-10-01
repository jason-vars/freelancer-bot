import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

// When the worker last wrote a job. The Jobs page polls this (cheap: one indexed
// row) and only re-renders the list when it changes.
export const dynamic = "force-dynamic";

export async function GET() {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("jobs").select("synced_at").order("synced_at", { ascending: false }).limit(1).maybeSingle();
  if (error) return NextResponse.json({ ok: false, message: error.message }, { status: 401 });
  return NextResponse.json({ ok: true, version: data?.synced_at ?? null }, { headers: { "Cache-Control": "no-store" } });
}
