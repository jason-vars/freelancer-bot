/** Shown instead of a page when server-side setup is wrong (missing env var, schema
 *  not run...). Next.js hides thrown error messages in production, so pages that
 *  depend on the secret key catch the error and render it with this. */
export function ServerError({ title, error }: { title: string; error: unknown }) {
  return (
    <div className="card mx-auto mt-6 max-w-2xl p-6">
      <h1 className="mb-2 text-xl font-semibold">{title}</h1>
      <p className="rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">
        {error instanceof Error ? error.message : String(error)}
      </p>
    </div>
  );
}
