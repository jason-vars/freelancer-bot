import { NextResponse } from "next/server";
import { userFromApiKey } from "@/lib/apikey";
import { NO_API_KEY, userscriptRequest } from "@/lib/userscriptApi";

// The userscript's API on the website. Current userscripts call the Supabase
// `generate` function instead (no cost to the web host); this stays for older ones.

export const maxDuration = 60; // one OpenAI call for the proposal, plus pricing

export async function GET(req: Request, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  const userId = await userFromApiKey(req);
  if (!userId) return NextResponse.json({ ok: false, message: NO_API_KEY }, { status: 401 });
  const r = await userscriptRequest(action, userId, new URL(req.url).searchParams);
  return NextResponse.json(r.body, { status: r.status });
}
