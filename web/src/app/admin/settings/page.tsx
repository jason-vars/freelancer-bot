import Link from "next/link";
import { requireAdmin } from "@/lib/auth";
import { BUILTIN_DEFAULTS, GROUPS } from "@/lib/settings/fields";
import { getAppSettings } from "@/lib/settings/store";
import { ServerError } from "@/components/ServerError";
import { SettingsForm } from "@/components/SettingsForm";
import { saveAppSettings } from "../actions";

export default async function AdminSettingsPage() {
  await requireAdmin();
  let app;
  try {
    app = await getAppSettings();
  } catch (e) {
    return <ServerError title="Bot settings couldn't load" error={e} />;
  }
  const all = GROUPS.flatMap((g) => g.fields);
  const secretKeys = all.filter((f) => f.kind === "secret").map((f) => f.key);
  // Secrets never go to the browser; the form only shows whether one is saved.
  const values = Object.fromEntries(
    all.filter((f) => f.kind !== "secret").map((f) => [f.key, app[f.key] ?? BUILTIN_DEFAULTS[f.key] ?? ""]),
  );
  const groups = [
    ...GROUPS.filter((g) => g.scope === "admin").map((g) => ({ title: g.title, fields: g.fields })),
    ...GROUPS.filter((g) => g.scope === "user").map((g) => ({
      title: `Default for users: ${g.title}`,
      note: "Users who haven't saved their own settings use these. Each user can change them on their own settings page.",
      fields: g.fields,
    })),
  ];
  return (
    <div className="space-y-6">
      <div>
        <Link href="/admin" className="text-sm text-muted hover:text-fg">← Admin</Link>
        <h1 className="mt-1 text-2xl font-semibold">Bot settings</h1>
        <p className="mt-1 text-sm text-muted">
          Shared by everyone. The job worker reloads these at the start of every cycle.
        </p>
      </div>
      <SettingsForm groups={groups} values={values}
        secretsSet={secretKeys.filter((k) => (app[k] ?? "").trim())} action={saveAppSettings} />
    </div>
  );
}
