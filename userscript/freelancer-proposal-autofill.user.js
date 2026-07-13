// ==UserScript==
// @name         Freelancer Bid Bot — Proposal Auto-Fill
// @namespace    freelancer-bid-bot
// @version      1.10.0
// @description  When you open a Freelancer project, fetch the bot-generated (OpenAI) proposal + bid amount + delivery days and fill the bid form automatically. Works even for projects the bot never collected — they're fetched live and filtered (incl. client country scraped from the page) before generating. Marks jobs 'applied' in the bot (on Place bid, or when it detects you've already bid) so the Jobs page shows what you've done.
// @match        https://www.freelancer.com/projects/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @updateURL    http://127.0.0.1:8765/userscript.user.js
// @downloadURL  http://127.0.0.1:8765/userscript.user.js
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
  // look up an already-collected project without the numeric id.
  function currentSeo() {
    let p = location.pathname.replace(/^\/+/, "").replace(/\/+$/, "");
    p = p.replace(/^projects\//, "").replace(/\/details$/, "");
    return p || null;
  }

  // ── Scrape the numeric project id from the page ───────────────────────────
  // The seo slug only finds projects the bot ALREADY collected. To also generate
  // for projects you merely browsed to (which the backend fetches live), we send
  // the numeric id too. Freelancer renders it as a "Project ID: 40572991" label;
  // we fall back to canonical/og URLs and the SPA's embedded state. Returns a
  // string of digits or null (in which case only collected projects will resolve).
  function currentProjectId() {
    const txt = (document.body && document.body.innerText) || "";
    let m = txt.match(/project\s*id[\s:#]*([0-9]{5,})/i);
    if (m) return m[1];
    const metas = document.querySelectorAll(
      'link[rel="canonical"], meta[property="og:url"], meta[name="twitter:url"]'
    );
    for (const el of metas) {
      const v = el.getAttribute("href") || el.getAttribute("content") || "";
      m = v.match(/[?&]project[_-]?id=(\d{5,})/i) || v.match(/-(\d{6,})(?:[/?#]|$)/);
      if (m) return m[1];
    }
    const html = (document.documentElement && document.documentElement.innerHTML) || "";
    m = html.match(/"project[_]?[iI]d"\s*:\s*(\d{5,})/);
    if (m) return m[1];
    return null;
  }

  // ── Read the "About the Client" panel text (for the country filter) ────────
  // Freelancer's API hides the client's country from our token, so we read what the
  // page shows and let the bot match BOT_SKIP_COUNTRIES / BOT_ALLOW_COUNTRIES against
  // it. We send the WHOLE "About the Client" text block (not a lone country word):
  // Freelancer renders the flag as an emoji (🇮🇳), so element-based flag scraping
  // missed it — but the country name ("India") is always present as plain text in
  // this block, and the bot matches country names within it. Returns the text (capped)
  // or null (then no country filter is applied and it generates as before).
  function currentClientCountry() {
    const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
    let head = null;
    for (const el of document.querySelectorAll("h1,h2,h3,h4,h5,strong,span,div")) {
      const t = clean(el.textContent);
      if (t.length < 40 && /about the client/i.test(t)) { head = el; break; }
    }
    if (!head) return null;
    // Climb to the card that holds the client's location/verification, but not so far
    // that we swallow the project description (which could mention other countries).
    let card = head;
    for (let i = 0; i < 6 && card.parentElement; i++) {
      card = card.parentElement;
      if ((card.innerText || "").length > 400) break;
    }
    const txt = clean(card.innerText || card.textContent);
    return txt ? txt.slice(0, 300) : null;
  }

  // ── Detect that YOU have already bid on this project ──────────────────────
  // Freelancer replaces the bid form with a "retract / revise your bid" UI once
  // you've bid. We match ONLY phrases that appear AFTER you've bid — never text
  // shown on a fresh bid form. (In particular NOT "edit your bid": the fresh form
  // says "You will be able to edit your bid until the project is awarded", which
  // used to false-flag every open project.) If the proposal textarea is present
  // we treat the page as biddable regardless — see waitForBidState.
  function hasAlreadyBid() {
    const ta = findProposalTextarea();
    // An EMPTY proposal box means the fresh bid form is open => you haven't bid yet.
    if (ta && !(ta.value || "").trim()) return false;
    // Otherwise require a STRONG, current signal: a visible Retract control (exists only
    // when you have an active bid) or the explicit "already placed a bid" message.
    // Loose body-text scanning used to false-fire on slow/partial loads and SPA
    // transitions (stale text), flagging fresh projects as already bid.
    for (const c of document.querySelectorAll('button, a, [role="button"]')) {
      if (c.offsetParent !== null && /\bretract\b/i.test((c.textContent || "").trim())) return true;
    }
    const txt = (document.body && document.body.innerText) || "";
    return /you(?:'ve| have)?\s+already\s+placed\s+a\s+bid/i.test(txt);
  }

  // ── Floating control panel: status + manual buttons (always available, even
  // when auto-fill couldn't find the bid box on a slow page) ─────────────────
  let statusEl = null;
  function mkBtn(label, color, onClick) {
    const b = document.createElement("button");
    b.textContent = label;
    b.style.cssText =
      "flex:1;cursor:pointer;border:0;border-radius:8px;padding:8px 6px;color:#fff;" +
      "font:600 12px system-ui,sans-serif;background:" + color + ";";
    b.addEventListener("click", (e) => { e.preventDefault(); onClick(); });
    return b;
  }
  function buildPanel() {
    if (statusEl) return;
    const panel = document.createElement("div");
    panel.style.cssText =
      "position:fixed;z-index:2147483647;right:16px;bottom:16px;display:flex;flex-direction:column;" +
      "gap:8px;width:250px;padding:12px;border-radius:12px;background:#12172a;border:1px solid #2c3550;" +
      "box-shadow:0 8px 30px rgba(0,0,0,.45);font:600 13px system-ui,sans-serif;color:#cdd5ee;";
    statusEl = document.createElement("div");
    statusEl.style.cssText = "padding:6px 8px;border-radius:8px;background:#1e2740;line-height:1.35;";
    statusEl.textContent = "🤖 Bid bot ready";
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;";
    row.appendChild(mkBtn("✨ Generate", "#3b82f6", () => run(true)));
    row.appendChild(mkBtn("🚀 Place bid", "#e0218a", () => placeBid()));
    const hint = document.createElement("div");
    hint.style.cssText = "font-weight:400;font-size:11px;color:#7a85a6;";
    hint.textContent = "Alt+G generate · Alt+B place bid · Alt+S seal";
    panel.appendChild(statusEl);
    panel.appendChild(row);
    panel.appendChild(hint);
    document.body.appendChild(panel);
  }
  function badge(text, kind) {
    if (!statusEl) buildPanel();
    const colors = {
      ok: "background:#10341f;color:#7ee2a8;",
      err: "background:#3a1717;color:#f8a3a3;",
      info: "background:#1e2740;color:#cdd5ee;",
    };
    statusEl.style.cssText =
      "padding:6px 8px;border-radius:8px;line-height:1.35;" + (colors[kind] || colors.info);
    statusEl.textContent = "🤖 " + text;
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
  // The bid proposal box is specifically <textarea id="descriptionTextArea">. We
  // require THIS element: it's the only thing that means "you can bid here", so the
  // script never generates a proposal (spends OpenAI) on pages without a real bid
  // form, and never fills an unrelated textarea (e.g. the Clarification Board box).
  function findProposalTextarea() {
    const ta =
      document.querySelector("textarea#descriptionTextArea") ||
      document.querySelector('textarea[name="descriptionTextArea"]');
    if (!ta || ta.offsetParent === null || ta.readOnly || ta.disabled) return null;
    return ta;
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

  // The free "Sealed" upgrade checkbox (hides your bid from other freelancers).
  // Preferred anchors are Freelancer's stable attributes (fltrackinglabel /
  // data-upgrade-type); the checkbox lives in a sibling subtree of the "Sealed" tag,
  // ~7 levels away, which the old shallow text-climb never reached. We require the row
  // to read "free" so we only auto-tick it when the upgrade is actually free.
  function findSealedCheckbox() {
    const anchors = document.querySelectorAll(
      'fl-list-item[fltrackinglabel="BidFormUpgrades.Sealed"], fl-upgrade-tag[data-upgrade-type="sealed"]'
    );
    for (const a of anchors) {
      const row = a.closest("fl-list-item") || a.parentElement;
      if (!row) continue;
      const cb = row.querySelector('input[type="checkbox"]');
      if (cb && /\bfree\b/i.test(row.textContent || "")) return cb;
    }
    // Fallback: find the "Sealed" leaf and climb far enough to reach checkbox + "free".
    const leaves = Array.from(document.querySelectorAll("*")).filter(
      (el) => !el.children.length && /^\s*sealed\s*$/i.test(el.textContent || "")
    );
    for (const leaf of leaves) {
      let row = leaf;
      for (let i = 0; i < 10 && row; i++) {
        const cb = row.querySelector ? row.querySelector('input[type="checkbox"]') : null;
        const txt = (row.textContent || "").toLowerCase();
        if (cb && txt.includes("sealed") && txt.includes("free")) return cb;
        row = row.parentElement;
      }
    }
    return null;
  }
  function checkSealed() {
    const cb = findSealedCheckbox();
    if (!cb) return false;
    if (!cb.checked) {
      // Click the LABEL (fl-checkbox hides the native input); the label's `for` toggles
      // it and fires Angular's handler. Falls back to clicking the input directly.
      const esc = (window.CSS && CSS.escape) ? CSS.escape(cb.id) : cb.id;
      const label = cb.id ? document.querySelector('label[for="' + esc + '"]') : null;
      (label || cb).click();
    }
    return cb.checked;
  }

  // Freelancer's submit button is "Place Bid" or "Create Bid" (never "Write my bid",
  // which is their own AI writer — excluded by the verb list).
  function findPlaceBidButton() {
    const btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
    return (
      btns.find(
        (b) => b.offsetParent !== null && /(place|update|submit)\s*bid/i.test((b.textContent || "").trim())
      ) || null
    );
  }
  function placeBid() {
    const btn = findPlaceBidButton();
    if (!btn) { badge("Place-bid button not found (is the bid form open?).", "err"); return; }
    btn.click();
    markApplied(currentSeo(), currentProjectId()); // record it in the bot's Jobs list
    badge("Clicked Place Bid — marked applied.", "ok");
  }

  // ── Tell the bot you've applied so the Jobs page shows it as done ──────────
  // Fire-and-forget: a failure here must never disrupt bidding. The bot stores the
  // project (fetching it live if it never collected it) with status 'applied'.
  function markApplied(seo, pid) {
    if (!seo && !pid) return;
    const params =
      (pid ? "id=" + encodeURIComponent(pid) : "") +
      (seo ? (pid ? "&" : "") + "seo=" + encodeURIComponent(seo) : "");
    try {
      GM_xmlhttpRequest({
        method: "GET",
        url: BOT_BASE + "/jobs/applied?" + params,
        timeout: 15000,
        onload: () => {}, onerror: () => {}, ontimeout: () => {},
      });
    } catch (e) { /* ignore */ }
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
    // Auto-enable the free Sealed upgrade whenever it's offered.
    if (checkSealed()) filled.push("sealed");
    return filled;
  }

  // ── Talk to the bot (GM_xmlhttpRequest bypasses CORS + mixed-content) ──────
  // Resolves with the parsed JSON for any well-formed response (including a
  // {ok:false, skipped:true} filter-skip); rejects only on transport/parse errors.
  function fetchProposal(seo, pid, country) {
    const params =
      "seo=" + encodeURIComponent(seo) +
      (pid ? "&id=" + encodeURIComponent(pid) : "") +
      (country ? "&country=" + encodeURIComponent(country) : "");
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "GET",
        url: BOT_BASE + "/jobs/generate?" + params,
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

  // ── Wait for the bid form to render, OR for an "already bid" state ─────────
  // Freelancer is a slow SPA. Resolves the instant the proposal textarea appears
  // ("textarea"), or the instant we can tell you've already bid ("alreadybid"),
  // via a MutationObserver with a polling fallback and a hard timeout ("timeout").
  function waitForBidState(timeoutMs) {
    const check = () =>
      hasAlreadyBid() ? "alreadybid" : (findProposalTextarea() ? "textarea" : null);
    return new Promise((resolve) => {
      const first = check();
      if (first) return resolve(first);
      let done = false;
      const finish = (val) => {
        if (done) return;
        done = true;
        try { obs.disconnect(); } catch (e) {}
        clearInterval(poll);
        clearTimeout(timer);
        resolve(val);
      };
      const obs = new MutationObserver(() => {
        const s = check();
        if (s) finish(s);
      });
      obs.observe(document.documentElement, { childList: true, subtree: true });
      // Belt-and-suspenders: some frameworks reveal the box without a mutation the
      // observer surfaces (e.g. an existing node un-hidden via CSS).
      const poll = setInterval(() => {
        const s = check();
        if (s) finish(s);
      }, 600);
      const timer = setTimeout(() => finish("timeout"), timeoutMs);
    });
  }

  let lastSeo = null;
  async function run(force) {
    const seo = currentSeo();
    if (!seo) return;
    if (!force && seo === lastSeo) return; // already handled this project in-tab
    lastSeo = seo;

    const state = await waitForBidState(90000);
    if (state === "alreadybid") {
      // You've already bid — skip generating (don't overwrite your bid). We do NOT mark
      // applied here: auto-detect can mis-fire on bad networks, and the applied list is
      // kept accurate by the panel's Place-bid and the "Sync applied" button instead.
      badge("You've already bid on this — skipping. Use Sync to refresh the list.", "info");
      return;
    }
    if (state !== "textarea") {
      // 90s and still nothing — either you can't bid here (closed / NDA not accepted)
      // or the page is extremely slow. Use the Generate button (or Alt+G) to retry.
      badge("No bid box yet — press Generate / Alt+G to retry.", "info");
      return;
    }

    try {
      let data;
      const cacheKey = "fbb:" + seo;
      const cached = force ? null : CACHE.getItem(cacheKey);
      if (cached) {
        data = JSON.parse(cached);
      } else {
        const country = currentClientCountry();
        // Surface what we scraped so a country-filter miss is diagnosable at a glance:
        // a short preview of the client text means the filter got something; "?" means
        // the "About the Client" block wasn't found, so the country filter can't apply.
        badge("Generating proposal… (client: " + (country ? country.slice(0, 32) : "?") + ")", "info");
        data = await fetchProposal(seo, currentProjectId(), country);
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

  // ── Keyboard shortcuts (Alt+letter so they don't fire while typing a bid) ──
  document.addEventListener("keydown", (e) => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const k = (e.key || "").toLowerCase();
    if (k === "g") { e.preventDefault(); run(true); }
    else if (k === "b") { e.preventDefault(); placeBid(); }
    else if (k === "s") { e.preventDefault(); badge(checkSealed() ? "Sealed checked." : "Sealed option not found.", "info"); }
  });

  // Build the panel immediately so the buttons exist even if the bid box never
  // loads, then run once. A light watcher re-runs on SPA navigation to a new project.
  buildPanel();
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
