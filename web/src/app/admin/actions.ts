"use server";

import { revalidatePath } from "next/cache";
import { getProfile } from "@/lib/auth";
import { GROUPS } from "@/lib/settings/fields";
import { validate } from "@/lib/settings/store";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";
import type { SaveState } from "@/components/SettingsForm";

async function adminOnly() {
  const p = await getProfile();
  if (!p || p.role !== "admin" || p.status !== "approved") throw new Error("Admins only.");
  return p;
}

export async function setUserStatus(userId: string, status: "approved" | "rejected" | "pending") {
  const me = await adminOnly();
  if (userId === me.id) return;
  const supabase = await createClient();
  await supabase.from("profiles").update({
    status, approved_at: status === "approved" ? new Date().toISOString() : null,
  }).eq("id", userId);
  revalidatePath("/admin");
}

export async function setUserRole(userId: string, role: "user" | "admin") {
  const me = await adminOnly();
  if (userId === me.id) return; // never lock yourself out
  const supabase = await createClient();
  await supabase.from("profiles").update({ role }).eq("id", userId);
  revalidatePath("/admin");
}

export async function deleteUser(userId: string) {
  const me = await adminOnly();
  if (userId === me.id) return;
  // Deleting the auth user cascades to profiles, user_settings and user_jobs.
  await createAdminClient().auth.admin.deleteUser(userId);
  revalidatePath("/admin");
}

export async function requestFetch() {
  await adminOnly();
  const supabase = await createClient();
  await supabase.from("worker_status").update({ run_requested_at: new Date().toISOString() }).eq("id", 1);
  revalidatePath("/admin");
}

export async function saveAppSettings(_prev: SaveState, form: FormData): Promise<SaveState> {
  try {
    await adminOnly();
  } catch {
    return { ok: false, message: "Admins only." };
  }
  // Admin settings plus the user-scope fields, whose admin value is every user's default.
  const { updates, errors } = validate(form, GROUPS.flatMap((g) => g.fields));
  if (Object.keys(errors).length) return { ok: false, message: "Fix the highlighted fields.", errors };
  const now = new Date().toISOString();
  const supabase = await createClient();
  const { error } = await supabase.from("app_settings").upsert(
    Object.entries(updates).map(([key, value]) => ({ key, value, updated_at: now })),
  );
  if (error) return { ok: false, message: error.message };
  revalidatePath("/admin/settings");
  return { ok: true, message: "Saved. The worker picks this up on its next cycle." };
}
