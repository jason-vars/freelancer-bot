/** Freelancer page for a stored `url` (the project's seo path, or already a full URL).
 *  No server imports: the Jobs page uses it in the browser. */
export function jobUrl(seo: string | null): string {
  if (!seo) return "";
  if (/^https?:\/\//.test(seo)) return seo;
  return `https://www.freelancer.com/projects/${seo.replace(/^\/+/, "")}`;
}
