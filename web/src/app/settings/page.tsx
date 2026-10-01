import { headers } from "next/headers";
import { requireApproved } from "@/lib/auth";
import { GROUPS } from "@/lib/settings/fields";
import { getAppSettings, getUserValues, userFormValues } from "@/lib/settings/store";
import { SettingsForm } from "@/components/SettingsForm";
import { saveUserSettings } from "./actions";
import { ApiKey } from "./ApiKey";

export default async function SettingsPage() {
  const profile = await requireApproved();
  const [app, mine] = await Promise.all([getAppSettings(), getUserValues(profile.id)]);
  const values = userFormValues(app, mine);
  const h = await headers();
  const origin = `${h.get("x-forwarded-proto") ?? "https"}://${h.get("host")}`;
  const groups = GROUPS.filter((g) => g.scope === "user").map((g) => ({ title: g.title, fields: g.fields }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">My settings</h1>
        <p className="mt-1 text-sm text-muted">
          How your proposals are written and what you bid. {mine ? "" : "These start from the admin's defaults until you save."}
        </p>
      </div>

      <section className="card space-y-4 p-5">
        <h2 className="text-lg font-semibold">Browser userscript</h2>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li>Install <a className="text-accent" href="https://www.tampermonkey.net/" target="_blank" rel="noreferrer">Tampermonkey</a> in your browser.</li>
          <li>Open <a className="text-accent" href={`${origin}/userscript.user.js`}>{origin}/userscript.user.js</a> and click Install.</li>
          <li>Create an API key below, then on any freelancer.com project page open the Tampermonkey menu → <b>Set bot API key</b> and paste it.</li>
        </ol>
        <p className="text-sm text-muted">The userscript writes your proposal into the bid form and marks the job Opened / Applied for you on the Jobs page.</p>
        <ApiKey prefix={profile.api_key_prefix} />
      </section>

      <SettingsForm groups={groups} values={values} action={saveUserSettings} />
    </div>
  );
}
