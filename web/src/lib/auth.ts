import "server-only";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type Profile = {
  id: string;
  email: string;
  full_name: string;
  role: "user" | "admin";
  status: "pending" | "approved" | "rejected";
  api_key_prefix: string | null;
  created_at: string;
  approved_at: string | null;
};

/** The signed-in user's profile, or null when signed out. */
export async function getProfile(): Promise<Profile | null> {
  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub;
  if (!uid) return null;
  const { data } = await supabase
    .from("profiles")
    .select("id,email,full_name,role,status,api_key_prefix,created_at,approved_at")
    .eq("id", uid)
    .maybeSingle();
  return (data as Profile | null) ?? null;
}

/** For pages only approved users may see. */
export async function requireApproved(): Promise<Profile> {
  const profile = await getProfile();
  if (!profile) redirect("/login");
  if (profile.status !== "approved") redirect("/pending");
  return profile;
}

export async function requireAdmin(): Promise<Profile> {
  const profile = await requireApproved();
  if (profile.role !== "admin") redirect("/jobs");
  return profile;
}
