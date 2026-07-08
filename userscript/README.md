# Freelancer proposal auto-fill (browser userscript)

Auto-fills the Freelancer bid form with the **bot-generated (OpenAI) proposal**,
**bid amount**, and **delivery days** when you open a project the bot already
collected.

## Why a userscript?

The proposal textarea lives on `freelancer.com` — a different site from your bot.
Browsers isolate origins, so neither the bot nor its Jobs page can type into a
freelancer.com tab. Code that runs *on* freelancer.com can. A userscript is the
lightest way to do that (no extension to package/sign).

## How it works

1. You open a project page: `https://www.freelancer.com/projects/<category>/<slug>/details`.
2. The script derives the seo slug `<category>/<slug>` from the URL — the exact
   string the bot stored as the project's `url` when it collected the job.
3. It calls your local bot: `GET http://127.0.0.1:8765/jobs/generate?seo=<slug>`
   (via `GM_xmlhttpRequest`, which bypasses CORS and https→localhost mixed-content).
4. The bot looks the project up, generates the proposal with your OpenAI key, and
   returns `{ proposal, amount, period, currency }`.
5. The script fills the bid textarea + amount + period, and shows a status badge
   (bottom-right). Click the badge to re-fill.

## Install

1. Install **Tampermonkey** (Chrome/Edge/Firefox) or **Violentmonkey**.
2. Tampermonkey → *Create a new script* → paste the contents of
   [`freelancer-proposal-autofill.user.js`](freelancer-proposal-autofill.user.js) →
   save. (Or open the `.user.js` file directly and Tampermonkey offers to install.)
3. Make sure the bot is running with the web UI: `python -m bot serve`
   (or `python -m bot webui`).
4. Open a project the bot alerted you about — the proposal fills in automatically.

## Requirements / notes

- The project must already be **in the bot's DB** (i.e. the bot collected/alerted
  it). If not, you'll see “No collected project matches …”. That's by design.
- **OpenAI cost:** auto-fill generates a proposal each time you open a *new*
  project. Refreshing the same project in the same tab reuses a cached result (no
  second call). Close the tab to clear the cache.
- If the bot runs on a **different host/port**, edit `BOT_BASE` at the top of the
  script (and the `@connect` line if it's not localhost).
- **If a field doesn't fill:** freelancer occasionally changes its bid-form DOM.
  The selectors live in `findProposalTextarea` / `findNumberInput` in the script —
  they use heuristics (largest visible textarea, name/label hints) and are easy to
  tweak. The proposal textarea is the reliable one; amount/period are best-effort.
