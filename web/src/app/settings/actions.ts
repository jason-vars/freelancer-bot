"use server";

import { revalidatePath } from "next/cache";
import { getProfile } from "@/lib/auth";
import { newApiKey } from "@/lib/apikey";
import { USER_KEYS } from "@/lib/settings/fields";
import { fieldsFor, getUserValues, validate } from "@/lib/settings/store";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";
import type { SaveState } from "@/components/SettingsForm";

export async function saveUserSettings(_prev: SaveState, form: FormData): Promise<SaveState> {
  const profile = await getProfile();
  if (!profile || profile.status !== "approved") return { ok: false, message: "Not signed in." };
  const { updates, errors } = validate(form, fieldsFor(USER_KEYS));
  if (Object.keys(errors).length) return { ok: false, message: "Fix the highlighted fields.", errors };

  // Written as the user, so RLS guarantees they can only touch their own row.
  const supabase = await createClient();
  const current = (await getUserValues(profile.id)) ?? {};
  const { error } = await supabase.from("user_settings").upsert({
    user_id: profile.id, values: { ...current, ...updates }, updated_at: new Date().toISOString(),
  });
  if (error) return { ok: false, message: error.message };
  revalidatePath("/settings");
  return { ok: true, message: "Saved." };
}

export type KeyState = { key?: string; error?: string };

/** Create (or replace) the user's userscript API key. The key is returned once. */
export async function createApiKey(): Promise<KeyState> {
  const profile = await getProfile();
  if (!profile || profile.status !== "approved") return { error: "Not signed in." };
  const { key, hash, prefix } = newApiKey();
  // profiles is admin-write under RLS, so this one column goes through the secret key.
  const { error } = await createAdminClient().from("profiles")
    .update({ api_key_hash: hash, api_key_prefix: prefix }).eq("id", profile.id);
  if (error) return { error: error.message };
  revalidatePath("/settings");
  return { key };
}
