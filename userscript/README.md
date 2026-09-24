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
6. With **auto-bid ON** (the default), it then counts down 5 seconds and clicks
   Freelancer's own **Place Bid** button for you. Press **Esc** during the countdown
   to stop it.

## Install

1. Install **Tampermonkey** (Chrome/Edge/Firefox) or **Violentmonkey**.
2. Tampermonkey → *Create a new script* → paste the contents of
   [`freelancer-proposal-autofill.user.js`](freelancer-proposal-autofill.user.js) →
   save. (Or open the `.user.js` file directly and Tampermonkey offers to install.)
3. Make sure the bot is running with the web UI: `python -m bot serve`
   (or `python -m bot webui`).
4. Open a project the bot alerted you about — the proposal fills in automatically.

## Controls (floating panel, bottom-right)

A small panel is always shown on a project page, so the actions work even when
auto-fill couldn't find the bid box (e.g. a slow page, or you opened the form late):

| Action | Button | Shortcut | What it does |
|---|---|---|---|
| Generate & fill | ✨ Generate | **Alt+G** | Re-fetch the proposal from the bot and fill the bid form (proposal + amount + period). |
| Place bid | 🚀 Place bid | **Alt+B** | Clicks Freelancer's own **Place Bid** / **Create Bid** button. |
| Seal | 🔒 Seal: ON/OFF | **Alt+S** | Turns the free **Sealed** upgrade on/off. ON = every fill ticks it; flipping it also seals/unseals the form that's already open, **and saves to the bot's Settings page** (`BOT_SEAL_BIDS`). |
| Copy job | 📋 Copy job | **Alt+C** | Copies the job's **skills + full description** to the clipboard (from the bot, so it's the untruncated text — no OpenAI call). |
| Auto-bid | 🤖 Auto-bid: ON/OFF | **Alt+A** | Turns automatic placing on/off. Remembered per browser. |
| Cancel | — | **Esc** | Aborts a running auto-bid countdown. |

The status line at the top of the panel shows what happened (filled fields, skip
reason, or errors).

### Auto-behaviours on fill
- **Sealed upgrade** — when sealing is ON (the default) and Freelancer offers the free
  *Sealed* entry (hide your bid from other freelancers), it's checked automatically.
  Only the *free* Sealed option is touched — paid upgrades (Sponsored, Highlight) are
  never enabled.

  The switch lives in the **bot's Settings page** → *Bid defaults* → **Seal bids (free
  upgrade)** (`BOT_SEAL_BIDS`). The panel's 🔒 button and **Alt+S** toggle that same
  setting (the script writes it via `GET /jobs/seal?set=0|1`), so the two can never
  disagree; the current value also travels back with every proposal. If the bot is
  unreachable, the button falls back to a per-browser value (`SEAL_DEFAULT` in the
  script) and says so.
- **Auto-bid** — once the proposal lands in the box, the panel counts down and then
  clicks **Place Bid** itself. Guard rails:
  - Only when the **proposal** actually filled — never on an empty/leftover box.
  - It waits (up to 15s) for Freelancer's button to become *enabled*, i.e. for their
    own form validation to pass, so amount/period problems stop the bid.
  - At most **once per project per tab** — re-generating or an SPA re-render can't
    double-submit — and it re-checks "already bid" immediately before clicking.
  - **Esc** cancels, and so does pressing ✨ Generate again (the countdown restarts
    with the new text).
- Change the 5s window with `AUTO_BID_DELAY_MS` at the top of the script (`0`
  submits immediately), or set `AUTO_BID_DEFAULT = false` to have it start OFF.

> **Place Bid is a real action.** It submits the bid on Freelancer (subject to
> Freelancer's own confirmation, if any) and spends a bid credit. With auto-bid ON
> that happens without a click, so keep the panel in view — or press **Alt+A** to
> turn it off and go back to placing bids by hand.

## Copy job (clipboard)

📋 **Copy job** / **Alt+C** puts this on your clipboard:

```
Skills: Next.js, Stripe, ...

<full description>
```

The text comes from the bot (`GET /jobs/text`), not scraped off the page, so you get
the complete description without Freelancer's "read more" truncation. It costs no
OpenAI call and ignores your skip filters, so it works on any project — even one the
bot would never bid on. A project the bot never collected is fetched live from the API.

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
