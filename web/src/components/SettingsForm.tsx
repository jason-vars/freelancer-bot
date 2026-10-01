"use client";

import { useActionState, useState } from "react";
import type { Field } from "@/lib/settings/fields";

export type SaveState = { ok?: boolean; message?: string; errors?: Record<string, string> };
export type FormGroup = { title: string; note?: string; fields: Field[] };

const TRUE = ["1", "true", "yes", "y", "on"];

export function SettingsForm({ groups, values, secretsSet = [], action, submitLabel = "Save settings" }: {
  groups: FormGroup[];
  values: Record<string, string>;
  secretsSet?: string[];
  action: (prev: SaveState, form: FormData) => Promise<SaveState>;
  submitLabel?: string;
}) {
  const [state, formAction, pending] = useActionState(action, {});
  const errors = state.errors ?? {};
  return (
    <form action={formAction} className="space-y-6">
      {groups.map((g) => (
        <section key={g.title} className="card p-5">
          <h2 className="text-lg font-semibold">{g.title}</h2>
          {g.note && <p className="mt-1 text-sm text-muted">{g.note}</p>}
          <div className="mt-4 grid gap-5 md:grid-cols-2">
            {g.fields.map((f) => (
              <FieldInput key={f.key} field={f} value={values[f.key] ?? ""}
                secretSet={secretsSet.includes(f.key)} error={errors[f.key]} />
            ))}
          </div>
        </section>
      ))}
      <div className="sticky bottom-0 -mx-4 flex items-center gap-3 border-t border-border bg-bg/90 px-4 py-3 backdrop-blur">
        <button className="btn btn-primary" disabled={pending}>{pending ? "Saving…" : submitLabel}</button>
        {state.message && (
          <span className={`text-sm ${state.ok ? "text-ok" : "text-bad"}`}>{state.message}</span>
        )}
      </div>
    </form>
  );
}

function FieldInput({ field: f, value, secretSet, error }: {
  field: Field; value: string; secretSet: boolean; error?: string;
}) {
  const wide = f.kind === "textarea" || f.kind === "bidrules" || f.kind === "telegrambots";
  const id = `f-${f.key}`;
  let control: React.ReactNode;
  switch (f.kind) {
    case "bool":
      return (
        <div className={wide ? "md:col-span-2" : ""}>
          <label className="flex items-start gap-3 text-sm">
            <input type="checkbox" name={f.key} value="1" defaultChecked={TRUE.includes(value.toLowerCase())}
              className="mt-0.5 h-4 w-4 accent-[var(--accent)]" />
            <span>
              <span className="font-medium">{f.label}</span>
              {f.help && <span className="help block">{f.help}</span>}
            </span>
          </label>
        </div>
      );
    case "textarea":
      control = <textarea id={id} name={f.key} defaultValue={value} rows={5} className="input font-mono text-[13px]" />;
      break;
    case "secret":
      control = (
        <input id={id} name={f.key} type="password" autoComplete="off" className="input"
          placeholder={secretSet ? "•••••••• saved — leave blank to keep" : "not set"} />
      );
      break;
    case "int":
      control = <input id={id} name={f.key} inputMode="numeric" defaultValue={value} placeholder={f.default} className="input" />;
      break;
    case "time":
      control = <input id={id} name={f.key} type="time" defaultValue={value} className="input" />;
      break;
    case "select": {
      const choices = f.choices ?? [];
      const known = choices.some(([v]) => v === value);
      control = (
        <select id={id} name={f.key} defaultValue={value || f.default} className="input">
          {value && !known && <option value={value}>{value} (custom)</option>}
          {choices.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      );
      break;
    }
    case "bidrules":
      control = <BidRules name={f.key} initial={value} />;
      break;
    case "telegrambots":
      control = <TelegramBots name={f.key} initial={value} />;
      break;
    default:
      control = <input id={id} name={f.key} defaultValue={value} placeholder={f.default} className="input" />;
  }
  return (
    <div className={wide ? "md:col-span-2" : ""}>
      <label className="label" htmlFor={id}>{f.label}</label>
      {control}
      {f.help && <p className="help">{f.help}</p>}
      {error && <p className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  );
}

function parseList<T>(raw: string): T[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

type Rule = { currencies: string; min: string; max: string; bid: string; delivery: string };

function BidRules({ name, initial }: { name: string; initial: string }) {
  const [rows, setRows] = useState<Rule[]>(() =>
    parseList<Record<string, unknown>>(initial).map((r) => ({
      currencies: ((r.currencies as string[]) ?? []).join(","),
      min: String(r.min ?? ""), max: String(r.max ?? ""), bid: String(r.bid ?? ""), delivery: String(r.delivery ?? ""),
    })),
  );
  const json = JSON.stringify(rows.map((r) => ({
    currencies: r.currencies.split(",").map((c) => c.trim()).filter(Boolean),
    min: r.min, max: r.max, bid: r.bid, delivery: r.delivery,
  })));
  const set = (i: number, k: keyof Rule, v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const cols: [keyof Rule, string][] = [["currencies", "Currencies (blank = any)"], ["min", "Budget min"], ["max", "Budget max"], ["bid", "Bid"], ["delivery", "Days"]];
  return (
    <div>
      <input type="hidden" name={name} value={rows.length ? json : ""} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              {cols.map(([, l]) => <th key={l} className="pb-1 pr-2 font-medium">{l}</th>)}<th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {cols.map(([k]) => (
                  <td key={k} className="pr-2 pb-2">
                    <input className="input min-w-20" value={r[k]} onChange={(e) => set(i, k, e.target.value)}
                      placeholder={k === "currencies" ? "USD,AUD" : ""} />
                  </td>
                ))}
                <td className="pb-2">
                  <button type="button" className="btn btn-danger" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="btn mt-1"
        onClick={() => setRows([...rows, { currencies: "", min: "", max: "", bid: "", delivery: "" }])}>+ Add rule</button>
    </div>
  );
}

function TelegramBots({ name, initial }: { name: string; initial: string }) {
  const [rows, setRows] = useState<{ token: string; chat_id: string }[]>(() =>
    parseList<{ token: string; chat_id: string }>(initial));
  const set = (i: number, k: "token" | "chat_id", v: string) =>
    setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div className="space-y-2">
      <input type="hidden" name={name} value={rows.length ? JSON.stringify(rows) : ""} />
      {rows.map((r, i) => (
        <div key={i} className="flex gap-2">
          <input className="input" placeholder="Bot token" value={r.token} onChange={(e) => set(i, "token", e.target.value)} />
          <input className="input max-w-40" placeholder="Chat id" value={r.chat_id} onChange={(e) => set(i, "chat_id", e.target.value)} />
          <button type="button" className="btn btn-danger" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button type="button" className="btn" onClick={() => setRows([...rows, { token: "", chat_id: "" }])}>+ Add bot</button>
    </div>
  );
}
