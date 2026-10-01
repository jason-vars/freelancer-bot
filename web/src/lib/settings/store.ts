import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { ALL_FIELDS, BUILTIN_DEFAULTS, type Field, USER_KEYS } from "./fields";

export type Values = Record<string, string>;

const TRUE = new Set(["1", "true", "yes", "y", "on"]);
export const isTrue = (v: string | undefined) => TRUE.has((v ?? "").trim().toLowerCase());

function validHHMM(v: string): boolean {
  const m = /^(\d{1,2}):(\d{2})$/.exec(v);
  return !!m && +m[1] <= 23 && +m[2] <= 59;
}

function normalizeBidRules(raw: string): [string | null, string] {
  if (!raw.trim()) return ["", ""];
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return [null, "Bid rules are malformed (could not parse)."];
  }
  if (!Array.isArray(data)) return [null, "Bid rules must be a list."];
  const out = [];
  for (const r of data) {
    if (!r || typeof r !== "object") continue;
    const rr = r as Record<string, unknown>;
    const currencies = (Array.isArray(rr.currencies) ? rr.currencies : [])
      .map((c) => String(c).trim().toUpperCase()).filter(Boolean);
    const rule = {
      currencies,
      min: Number(rr.min || 0),
      max: Number(rr.max || 0),
      bid: Number(rr.bid || 0),
      delivery: Math.trunc(Number(rr.delivery || 0)),
    };
    if ([rule.min, rule.max, rule.bid, rule.delivery].some((n) => Number.isNaN(n)))
      return [null, "Bid rules contain a non-numeric value."];
    if (rule.max < rule.min) return [null, "A bid rule has Max smaller than Min."];
    out.push(rule);
  }
  return [JSON.stringify(out), ""];
}

function normalizeTelegramBots(raw: string): [string | null, string] {
  if (!raw.trim()) return ["", ""];
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return [null, "Extra bots are malformed (could not parse)."];
  }
  if (!Array.isArray(data)) return [null, "Extra bots must be a list."];
  const out = [];
  for (const r of data) {
    if (!r || typeof r !== "object") continue;
    const token = String((r as Record<string, unknown>).token ?? "").trim();
    const chat_id = String((r as Record<string, unknown>).chat_id ?? "").trim();
    if (!token && !chat_id) continue;
    if (!token || !chat_id) return [null, "Each extra bot needs BOTH a token and a chat id."];
    out.push({ token, chat_id });
  }
  return [JSON.stringify(out), ""];
}

/** Validate a submitted settings form for the given fields. Returns the values to
 *  store and per-field errors. A blank secret is left out, so it keeps its value. */
export function validate(form: FormData, fields: Field[]): { updates: Values; errors: Values } {
  const updates: Values = {};
  const errors: Values = {};
  for (const f of fields) {
    const raw = String(form.get(f.key) ?? "");
    const val = raw.trim();
    switch (f.kind) {
      case "secret":
        if (val) updates[f.key] = val;
        break;
      case "bool":
        updates[f.key] = isTrue(val) ? "1" : "0";
        break;
      case "int":
        if (val === "") updates[f.key] = "";
        else if (!/^\d+$/.test(val)) errors[f.key] = "Must be a whole number, 0 or greater.";
        else updates[f.key] = String(parseInt(val, 10));
        break;
      case "time":
        if (val === "" || validHHMM(val)) updates[f.key] = val;
        else errors[f.key] = "Use HH:MM (24-hour).";
        break;
      case "textarea":
        updates[f.key] = raw.replace(/\r/g, "").trim();
        break;
      case "bidrules": {
        const [v, err] = normalizeBidRules(raw);
        if (v === null) errors[f.key] = err;
        else updates[f.key] = v;
        break;
      }
      case "telegrambots": {
        const [v, err] = normalizeTelegramBots(raw);
        if (v === null) errors[f.key] = err;
        else updates[f.key] = v;
        break;
      }
      default:
        updates[f.key] = val;
    }
  }
  return { updates, errors };
}

/** Every admin setting (secrets included). Server only. */
export async function getAppSettings(): Promise<Values> {
  const { data, error } = await createAdminClient().from("app_settings").select("key,value");
  if (error) {
    const hint = /api key|jwt|unauthori[sz]ed|401/i.test(error.message)
      ? " Check that SUPABASE_SECRET_KEY in Vercel is a current secret key (sb_secret_…) of this Supabase project, then redeploy."
      : /does not exist|relation|schema cache/i.test(error.message)
        ? " Run supabase/schema.sql in the Supabase SQL Editor."
        : "";
    throw new Error(`Couldn't read app settings: ${error.message}.${hint}`);
  }
  return Object.fromEntries((data ?? []).map((r) => [r.key as string, (r.value as string) ?? ""]));
}

export async function getUserValues(userId: string): Promise<Values | null> {
  const { data } = await createAdminClient()
    .from("user_settings").select("values").eq("user_id", userId).maybeSingle();
  return (data?.values as Values | undefined) ?? null;
}

/** What a user's proposals actually use: built-in defaults, overlaid by the admin's
 *  values, overlaid (for user-scope keys only) by the user's own saved values. */
export async function getEffectiveSettings(userId: string): Promise<Values> {
  const [app, mine] = await Promise.all([getAppSettings(), getUserValues(userId)]);
  const merged: Values = { ...BUILTIN_DEFAULTS, ...app };
  for (const key of USER_KEYS) {
    if (mine && key in mine) merged[key] = String(mine[key] ?? "");
  }
  return merged;
}

/** The values a user's settings form starts from: theirs if saved, else the admin's
 *  defaults, else the built-ins. */
export function userFormValues(app: Values, mine: Values | null): Values {
  const out: Values = {};
  for (const key of USER_KEYS) {
    out[key] = mine && key in mine ? String(mine[key] ?? "") : app[key] ?? BUILTIN_DEFAULTS[key] ?? "";
  }
  return out;
}

export function fieldsFor(keys: string[]): Field[] {
  return keys.map((k) => ALL_FIELDS[k]).filter(Boolean);
}
