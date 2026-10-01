"use client";

import Link from "next/link";
import { useActionState } from "react";
import type { AuthState } from "./actions";

export function AuthForm({ mode, action }: {
  mode: "login" | "register";
  action: (prev: AuthState, form: FormData) => Promise<AuthState>;
}) {
  const [state, formAction, pending] = useActionState(action, {});
  const register = mode === "register";
  return (
    <div className="mx-auto mt-10 max-w-sm">
      <h1 className="mb-1 text-2xl font-semibold">{register ? "Create an account" : "Sign in"}</h1>
      <p className="mb-6 text-sm text-muted">
        {register ? "An admin approves new accounts before you can see jobs." : "Welcome back."}
      </p>
      <form action={formAction} className="card space-y-4 p-5">
        {register && (
          <div>
            <label className="label" htmlFor="full_name">Name</label>
            <input className="input" id="full_name" name="full_name" required autoComplete="name" />
          </div>
        )}
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input className="input" id="email" name="email" type="email" required autoComplete="email" />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input className="input" id="password" name="password" type="password" required
            minLength={register ? 8 : undefined} autoComplete={register ? "new-password" : "current-password"} />
        </div>
        {state.error && <p className="rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">{state.error}</p>}
        {state.message && <p className="rounded-lg bg-ok-bg px-3 py-2 text-sm text-ok">{state.message}</p>}
        <button className="btn btn-primary w-full" disabled={pending}>
          {pending ? "Please wait…" : register ? "Create account" : "Sign in"}
        </button>
      </form>
      <p className="mt-4 text-center text-sm text-muted">
        {register ? <>Already registered? <Link className="text-accent" href="/login">Sign in</Link></>
          : <>No account yet? <Link className="text-accent" href="/register">Register</Link></>}
      </p>
    </div>
  );
}
