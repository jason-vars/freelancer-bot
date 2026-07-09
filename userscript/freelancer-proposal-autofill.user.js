// ==UserScript==
// @name         Freelancer Bid Bot — Proposal Auto-Fill
// @namespace    freelancer-bid-bot
// @version      1.0.0
// @description  When you open a Freelancer project the bot already collected, fetch the bot-generated (OpenAI) proposal + bid amount + delivery days and fill the bid form automatically.
// @match        https://www.freelancer.com/projects/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  "use strict";

  // ── Config ────────────────────────────────────────────────────────────────
  // Where your bot's web UI is listening (python -m bot webui / serve).
  const BOT_BASE = "http://127.0.0.1:8765";
  // Re-use an already-fetched proposal for the same project within this tab so a
  // page refresh doesn't spend a second OpenAI call. Cleared when the tab closes.
  const CACHE = window.sessionStorage;

  // ── Derive the project's seo slug from the page URL ───────────────────────
  // /projects/<category>/<slug>/details  ->  <category>/<slug>
  // That is exactly the string the bot stored as the project's `url`, so it can
  // look the project up without us scraping the numeric id out of the DOM.
  function currentSeo() {
    let p = location.pathname.replace(/^\/+/, "").replace(/\/+$/, "");
    p = p.replace(/^projects\//, "").replace(/\/details$/, "");
    return p || null;
  }

  // ── Small floating status badge so you can see what happened ──────────────
  let badgeEl = null;
  function badge(text, kind) {
    if (!badgeEl) {
      badgeEl = document.createElement("div");
      badgeEl.style.cssText =
        "position:fixed;z-index:2147483647;right:16px;bottom:16px;padding:9px 14px;" +
        "border-radius:9px;font:600 13px system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.35);" +
        "cursor:pointer;max-width:340px;";
      badgeEl.title = "Click to re-fill from the bot";
      badgeEl.addEventListener("click", () => run(true));
      document.body.appendChild(badgeEl);
    }
    const colors = {
      ok: "background:#10341f;color:#7ee2a8;border:1px solid #1c5a37;",
      err: "background:#3a1717;color:#f8a3a3;border:1px solid #6b2626;",
      info: "background:#1e2740;color:#cdd5ee;border:1px solid #2c3550;",
    };
    badgeEl.style.cssText += colors[kind] || colors.info;
    badgeEl.textContent = "🤖 " + text;
  }

  // ── Value setter that frameworks (Angular/React) actually notice ──────────
  function setNativeValue(el, value) {
    const proto =
      el.tagName === "TEXTAREA"
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    for (const type of ["input", "change", "blur"]) {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    }
  }

  // ── Field locators (freelancer's DOM changes; edit these if it stops working) ──
  function findProposalTextarea() {
    const candidates = Array.from(document.querySelectorAll("textarea")).filter(
      (t) => t.offsetParent !== null && !t.readOnly && !t.disabled
    );
    if (!candidates.length) return null;
    // Prefer one whose placeholder/label hints at a proposal/description.
    const hinted = candidates.find((t) =>
      /proposal|describe|cover|why.*hire|pitch/i.test(
        (t.placeholder || "") + " " + (t.getAttribute("name") || "") + " " + (t.id || "")
      )
    );
    // Otherwise the largest visible textarea — the bid box is the big one.
    return (
      hinted ||
      candidates.sort((a, b) => b.clientHeight - a.clientHeight)[0]
    );
  }

  function findNumberInput(hintRe) {
    const inputs = Array.from(
      document.querySelectorAll('input[type="number"], input[inputmode="numeric"], input')
    ).filter((i) => i.offsetParent !== null && !i.readOnly && !i.disabled);
    return (
      inputs.find((i) =>
        hintRe.test(
          (i.getAttribute("name") || "") + " " + (i.id || "") + " " +
          (i.getAttribute("formcontrolname") || "") + " " + (i.getAttribute("aria-label") || "")
        )
      ) || null
    );
  }

  function fillForm(data) {
    let filled = [];
    const ta = findProposalTextarea();
    if (ta) {
      setNativeValue(ta, data.proposal || "");
      filled.push("proposal");
    }
    if (data.amount != null) {
      const amt = findNumberInput(/amount|bid|budget/i);
      if (amt) { setNativeValue(amt, String(data.amount)); filled.push("amount"); }
    }
    if (data.period != null) {
      const per = findNumberInput(/period|day|deliver|duration/i);
      if (per) { setNativeValue(per, String(data.period)); filled.push("period"); }
    }
    return filled;
  }

  // ── Talk to the bot (GM_xmlhttpRequest bypasses CORS + mixed-content) ──────
  // Resolves with the parsed JSON for any well-formed response (including a
  // {ok:false, skipped:true} filter-skip); rejects only on transport/parse errors.
  function fetchProposal(seo) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "GET",
        url: BOT_BASE + "/jobs/generate?seo=" + encodeURIComponent(seo),
        timeout: 60000,
        onload: (r) => {
          let d;
          try { d = JSON.parse(r.responseText); } catch (e) { return reject("Bad response from bot."); }
          if (r.status >= 200 && r.status < 300) resolve(d);
          else reject(d.message || ("Bot returned HTTP " + r.status));
        },
        onerror: () => reject("Cannot reach the bot at " + BOT_BASE + " — is `python -m bot serve` running?"),
        ontimeout: () => reject("Bot timed out generating the proposal."),
      });
    });
  }

  // ── Wait for the bid form to render (freelancer is a SPA) ──────────────────
  function waitForTextarea(timeoutMs) {
    return new Promise((resolve) => {
      const t0 = Date.now();
      const tick = () => {
        if (findProposalTextarea()) return resolve(true);
        if (Date.now() - t0 > timeoutMs) return resolve(false);
        setTimeout(tick, 400);
      };
      tick();
    });
  }

  let lastSeo = null;
  async function run(force) {
    const seo = currentSeo();
    if (!seo) return;
    if (!force && seo === lastSeo) return; // already handled this project in-tab
    lastSeo = seo;

    if (!(await waitForTextarea(15000))) {
      badge("No bid box found on this page.", "info");
      return;
    }

    try {
      let data;
      const cacheKey = "fbb:" + seo;
      const cached = force ? null : CACHE.getItem(cacheKey);
      if (cached) {
        data = JSON.parse(cached);
      } else {
        badge("Generating proposal…", "info");
        data = await fetchProposal(seo);
      }
      // Project matched a currency/country skip filter — don't fill, don't cache.
      if (data && data.skipped) {
        badge(data.message || "Skipped by filter.", "info");
        return;
      }
      if (!data || !data.ok) {
        badge((data && data.message) || "Bot could not generate a proposal.", "err");
        return;
      }
      if (!cached) CACHE.setItem(cacheKey, JSON.stringify(data));
      const filled = fillForm(data);
      if (filled.includes("proposal")) badge("Filled: " + filled.join(", "), "ok");
      else badge("Got proposal but couldn't find the bid box.", "err");
    } catch (msg) {
      badge(String(msg), "err");
    }
  }

  // Initial run, plus a light watch for SPA navigations between projects.
  run(false);
  let lastPath = location.pathname;
  setInterval(() => {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      lastSeo = null;
      run(false);
    }
  }, 1000);
})();
