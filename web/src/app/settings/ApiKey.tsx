"use client";

import { useState, useTransition } from "react";
import { createApiKey, type KeyState } from "./actions";

export function ApiKey({ prefix }: { prefix: string | null }) {
  const [state, setState] = useState<KeyState>({});
  const [pending, start] = useTransition();
  const create = () => {
    if (prefix && !confirm("Replace your key? The userscript stops working until you paste the new one.")) return;
    start(async () => setState(await createApiKey()));
  };
  return (
    <div className="space-y-3">
      {state.key ? (
        <div className="rounded-lg bg-ok-bg p-3 text-sm">
          <p className="mb-2 text-ok">Your new key. Copy it now; it won&apos;t be shown again.</p>
          <code className="block break-all rounded bg-surface px-2 py-1.5 font-mono">{state.key}</code>
        </div>
      ) : (
        <p className="text-sm text-muted">
          {prefix ? <>Current key: <code className="font-mono">{prefix}…</code></> : "No key yet."}
        </p>
      )}
      {state.error && <p className="text-sm text-bad">{state.error}</p>}
      <button className="btn" onClick={create} disabled={pending}>
        {pending ? "Creating…" : prefix ? "Replace key" : "Create key"}
      </button>
    </div>
  );
}
