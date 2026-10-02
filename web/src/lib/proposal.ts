import "server-only";
import OpenAI from "openai";

// TypeScript port of bot/proposal_ai.py. Keep the two in step: the prompts and the
// deterministic post-processing (_sanitize, _assemble, ...) are what make proposals
// read the same whether they come from the web app or the Python bot.

// First line of every proposal reply: the client's required start word, or NONE.
const LEAD_MARKER = "LEAD:";

const DEFAULT_PROFILE_BULLETS = [
  "Senior engineer who has shipped many similar production projects end to end.",
  "Strong across the job's stack — modern web, APIs, mobile, and integrations.",
  "I build clean, modular, well-documented solutions and communicate daily.",
];

export type ProposalInput = {
  title: string;
  description: string;
  budgetMin: number | null;
  budgetMax: number | null;
  currency: string | null;
  skills: string;
  profileBullets: string[];
  portfolioUrls: string[];
  signatureName: string;
  extraInstructions: string;
  includeName: boolean;
  includeProfile: boolean;
  askQuestion: boolean;
  template: string;
  prefix: string;
  suffix: string;
  prefixInline: boolean;
};

function systemRules(o: {
  askQuestion: boolean; includeProfile: boolean; hasTemplate: boolean;
  hasPortfolio: boolean; userInstructions: string;
}): string {
  const custom = Boolean(o.userInstructions.trim());
  const rule = (soft: string, hard: string) => (custom ? soft : hard);

  const lines = [
    "You write short, human cover letters (proposals) for Freelancer.com jobs.",
    "Output ONLY the proposal text itself — no preamble, no headings, no markdown, no code fences.",
    "",
    rule("BASE RULES (defaults only — YOUR INSTRUCTIONS at the end override any of these):",
      "HARD RULES (never break these):"),
    rule("- English, unless your instructions ask for another language.", "- English only."),
    rule("- Keep it short: your own instructions set the exact length.",
      "- Keep it UNDER 200 words AND under 1200 characters."),
    rule("- Open in a way that shows you understand THIS specific project, following whatever " +
      "opening your instructions describe.",
      "- Open with a strong one-line hook that immediately shows you understand THIS specific project."),
    rule("- Plain text: no markdown, no code fences, no backslash (\\) characters. Numbered or " +
      "labelled lines (e.g. 'Q1:') where your instructions ask for them.",
      "- Plain text only: NO bullet points, NO numbered lists, NO markdown, NO backslash (\\) characters."),
    rule("- Your instructions decide the layout: line breaks, blank lines, paragraphs.",
      "- Write one sentence per line. Do NOT use empty lines, except at most a single blank line right before the closing."),
    rule("- Confident, natural, human. No fluff, no fake claims.",
      "- Confident, natural, human. No emojis, no fluff, no fake claims. Use ',' never ';'."),
  ];
  if (o.includeProfile && o.hasPortfolio) {
    lines.push(
      "- A tagged portfolio list follows. Each link shows the tech/role it demonstrates. " +
      "Include ONLY the link(s) whose tags best match THIS job's skills and description — " +
      "usually 1, at most 2-3. Omit every unrelated link. If none clearly fit, include none. " +
      rule("Place them where your instructions say, and never print the tags or invent links.",
        "Put each chosen link on its own line, and NEVER print the tags or invent links."),
    );
  } else if (!o.includeProfile) {
    lines.push("- Do NOT include portfolio links or a profile/experience dump.");
  }
  if (o.askQuestion && custom) {
    lines.push(
      "- Your own instructions below decide whether to ask questions, how many, and in what format. " +
      "Follow them exactly, including any fixed wording or Q1/Q2 labels they specify.",
    );
  } else if (o.askQuestion) {
    lines.push(
      "- Ask a question ONLY when the post leaves something genuinely unclear that would change HOW you build " +
      "it. In that case end with one or two short questions, each drawn from THIS post (their feature, data, " +
      "edge case, platform, or integration). NEVER a generic closer about deadlines, timelines, budget, or " +
      "when to start.",
    );
    lines.push(
      "- When the post is already clear enough to start, ask NOTHING. End instead with a short concrete plan: " +
      "the first two or three steps you would actually take on THIS job, one per line, naming their features.",
    );
  } else {
    lines.push("- Do NOT ask any questions.");
  }
  lines.push(
    "- Do NOT write a closing salutation (no 'Best regards', 'Sincerely', etc.) and do NOT write any name or " +
    "signature. End with the last sentence of your pitch or your question — a closing and signature are added " +
    "automatically afterwards.",
  );
  lines.push(
    "- Any analysis, extraction or classification step described in the instructions is SILENT: never print " +
    "its labels or results (e.g. 'The real goal is', 'Core friction', 'Client type', 'Niche'). Right after " +
    "the LEAD line below, the first words are the first words of the proposal itself.",
  );
  // One call does both jobs: the model reports the client's anti-AI start word on a
  // machine-readable first line, which the bot strips and places itself.
  lines.push(
    "- REQUIRED FIRST LINE (read by software and removed before anyone sees it, so it applies even if the " +
    `instructions say to output only the proposal): write '${LEAD_MARKER} <text>' when the job post tells ` +
    "bidders to begin their proposal with a specific word, code or phrase (an anti-AI check, e.g. 'start " +
    "your bid with the word Bundle'), copying that text exactly with its casing, and nothing else. " +
    `Otherwise write '${LEAD_MARKER} NONE'. Then start the proposal on the next line, without repeating that ` +
    "word — it is placed at the top automatically.",
  );

  if (o.hasTemplate) {
    lines.push(
      "",
      "Follow the STYLE, TONE and STRUCTURE of the user's template below, adapting its wording to THIS job. " +
      "Rewrite its job-specific content for this post, but KEEP whatever fixed framing lines the template " +
      "itself has (its greeting, any lead-in sentence before a question block, and the like), only " +
      "adjusting a number in them to match what you actually write. If the template has no such line, do " +
      "not add one. Drop only content sentences that do not apply to this job. Where the template and the " +
      "USER INSTRUCTIONS disagree, the instructions win.",
    );
  } else if (!custom) {
    lines.push(
      "",
      "Structure: the hook, then 1-2 sentences on your directly-relevant experience with this job's exact stack, " +
      "then a brief concrete approach written as flowing sentences (NOT a list), then the closing.",
    );
  }
  if (custom) {
    lines.push("", "USER INSTRUCTIONS (HIGHEST priority — if anything above conflicts with these, obey these):",
      o.userInstructions.trim());
  }
  return lines.join("\n");
}

function splitPortfolioEntry(entry: string): [string, string] {
  const e = (entry || "").trim();
  const bar = e.indexOf("|");
  if (bar >= 0) return [e.slice(0, bar).trim(), e.slice(bar + 1).trim()];
  const m = /^(\S+)(?:\t+|\s{2,})(.+)$/.exec(e);
  if (m) return [m[1].trim(), m[2].trim()];
  return [e, ""];
}

export function extractJson(text: string): Record<string, unknown> {
  if (!text) return {};
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch { /* fall through */ }
  const m = /\{[\s\S]*\}/.exec(text);
  if (m) {
    try {
      const v = JSON.parse(m[0]);
      return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch {
      return {};
    }
  }
  return {};
}

async function ask(client: OpenAI, model: string, system: string, user: string): Promise<string> {
  const resp = await client.responses.create({
    model,
    input: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
  });
  return (resp.output_text || "").trim();
}

/** Split the model's reply into [lead, proposal].
 *
 *  Some clients hide an anti-AI check in the post ("start your bid with the word
 *  Bundle"). The proposal call reports that text on its first line as
 *  `LEAD: <text>` (or `LEAD: NONE`) so no second OpenAI call is needed to find it.
 *  A reply without the line is treated as having no lead. */
export function splitLeadLine(text: string): [string, string] {
  const lines = (text || "").trim().split("\n");
  // Usually the first line, but a model that put it lower must still never leak
  // "LEAD: NONE" into the bid the client reads.
  for (let i = 0; i < lines.length; i++) {
    const m = /^\W*LEAD\W*:\s*(.*)$/i.exec(lines[i].trim());
    if (!m) continue;
    let lead = m[1].replace(/^[\s"'“”‘’*`]+|[\s"'“”‘’*`]+$/g, "");
    if (["NONE", "N/A", "NA", "NULL", "-", ""].includes(lead.toUpperCase())) lead = "";
    return [lead, [...lines.slice(0, i), ...lines.slice(i + 1)].join("\n").trim()];
  }
  return ["", (text || "").trim()];
}

export async function generateProposal(apiKey: string, model: string, d: ProposalInput): Promise<string> {
  const client = new OpenAI({ apiKey });
  const portfolio = d.portfolioUrls.filter((u) => u.trim());
  const bullets = d.profileBullets.length ? d.profileBullets : DEFAULT_PROFILE_BULLETS;
  const template = d.template.trim();
  const signature = d.signatureName.trim();

  const blocks = [
    `Job title: ${d.title}`,
    "",
    "Job description:",
    d.description || "(none)",
    "",
    `Required skills (weave the relevant ones in naturally): ${d.skills || "(none listed)"}`,
    `Budget: ${d.budgetMin}-${d.budgetMax} ${d.currency}`,
  ];
  if (d.includeProfile) {
    blocks.push(
      "",
      "MY FACTS — true facts about you (draw on these, do NOT invent others, do NOT list them verbatim):",
      bullets.map((b) => `- ${b}`).join("\n"),
    );
    if (portfolio.length) {
      const parsed = portfolio.map(splitPortfolioEntry);
      if (parsed.some(([, tags]) => tags)) {
        blocks.push(
          "",
          "Portfolio links with the tech/role each one demonstrates. Pick ONLY the " +
          "link(s) whose 'demonstrates' tags match this job; omit the rest; never print the tags:",
          parsed.map(([url, tags]) => (tags ? `- ${url}  (demonstrates: ${tags})` : `- ${url}`)).join("\n"),
        );
      } else {
        blocks.push("", "Portfolio URLs you may reference (only these, each on its own line):", portfolio.join("\n"));
      }
    }
  }
  if (template) blocks.push("", "USER TEMPLATE to follow (adapt its wording to this job):", template);
  // The reminder sits last because the end of the prompt is what models follow most.
  blocks.push("", `Write the proposal now, starting with the '${LEAD_MARKER}' line.`);

  const system = systemRules({
    askQuestion: d.askQuestion, includeProfile: d.includeProfile, hasTemplate: Boolean(template),
    hasPortfolio: portfolio.length > 0, userInstructions: d.extraInstructions,
  });
  // Its first line carries the client-required start word (or NONE), not proposal text.
  const [lead, rawBody] = splitLeadLine(await ask(client, model, system, blocks.join("\n")));

  let body = sanitize(rawBody);
  if (d.prefixInline) body = mergeGreetingLine(body);
  body = enforceTemplateFraming(body, template);
  // "Hi, You need ..." -> "Hi, you need ..." however the greeting got there.
  body = fixGreetingCase(localizeBodyGreeting(body));
  return assemble(stripLeadFromBody(body, lead), {
    lead, prefix: d.prefix, suffix: d.suffix,
    signature: d.includeName ? signature : "", prefixInline: d.prefixInline,
  });
}

const norm = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

function stripLeadFromBody(body: string, lead: string): string {
  const target = norm(lead);
  if (!target) return body;
  const lines = body.split(/\r?\n/);
  while (lines.length && !lines[0].trim()) lines.shift();
  if (lines.length && norm(lines[0]) === target) {
    lines.shift();
    while (lines.length && !lines[0].trim()) lines.shift();
    return lines.join("\n").trim();
  }
  return body;
}

// Greetings the bot recognises, in the languages proposals get written in (a
// Spanish post gets "Hola,"). Without them a non-English greeting looked missing
// and "Hi," was added in front of it.
const GREETINGS = "hi|hello|hey|good (?:morning|afternoon|evening)|hola|buen(?:os|as) (?:d[ií]as|tardes|noches)|buenas|bonjour|salut|hallo|guten (?:tag|morgen|abend)|ol[aá]|oi|bom dia|boa (?:tarde|noite)|ciao|salve|buongiorno|hej|hei|hoi|merhaba|namaste";
// End of a greeting word; \b misses accented endings like "olá".
const GREETING_END = String.raw`(?=[\s,!.:]|$)`;

const GREETING_LINE = new RegExp(`^(${GREETINGS}|dear [a-z .'-]{0,24})[,!:.]*$`, "i");

function mergeGreetingLine(text: string): string {
  const lines = text.split("\n");
  if (lines.length < 2 || !GREETING_LINE.test(lines[0].trim())) return text;
  const rest = lines.slice(1);
  while (rest.length && !rest[0].trim()) rest.shift();
  if (!rest.length) return text;
  return glueGreeting(lines[0].trim(), rest.join("\n"));
}

const LEAD_GREETING = new RegExp(String.raw`^\s*(${GREETINGS})${GREETING_END}[,!.:]*`, "i");
const ENGLISH_GREETING = /^\s*(hi|hello|hey|good (?:morning|afternoon|evening))\b/i;
const QUESTION_LINE = /^\s*Q\d+\s*[:.)]/i;
const QUESTION_COUNT = /\b(one|two|three|four|five|\d+)(\s+)questions?\b/i;
const COUNT_WORDS: Record<number, string> = { 1: "one", 2: "two", 3: "three", 4: "four", 5: "five" };
const I_WORDS = new Set(["i", "i'm", "i’m", "i'd", "i’d", "i've", "i’ve", "i'll", "i’ll"]);

function templateQuestionLeadIn(template: string): string {
  const lines = (template || "").split(/\r?\n/).map((l) => l.trim());
  for (let i = 0; i < lines.length; i++) {
    if (QUESTION_LINE.test(lines[i])) {
      const prev = [...lines.slice(0, i)].reverse().find((p) => p) ?? "";
      const isLeadIn = prev.endsWith(":") || prev.toLowerCase().includes("question");
      return isLeadIn && !QUESTION_LINE.test(prev) ? prev : "";
    }
  }
  return "";
}

function withQuestionCount(leadIn: string, n: number): string {
  const word = COUNT_WORDS[n] ?? String(n);
  return leadIn.replace(QUESTION_COUNT, (_m, g1: string, g2: string) => {
    const w = g1[0] === g1[0].toUpperCase() && /[a-z]/i.test(g1[0]) ? word[0].toUpperCase() + word.slice(1) : word;
    return `${w}${g2}${n === 1 ? "question" : "questions"}`;
  });
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Python's str.isupper(): has a cased letter and no lower-case one.
const isUpper = (w: string) => w === w.toUpperCase() && w !== w.toLowerCase();

// A post in another language gets a proposal in that language, and an English "Hi,"
// on top of it reads as a slip. These words identify the language (each list holds
// words common in that language and rare in the others), and the map gives the
// greeting to use instead.
const LANG_WORDS: Record<string, Set<string>> = {
  en: new Set("the and to of is for with you your this that will we it be on are".split(" ")),
  es: new Set("el los las del que y para con por una sin es lo como pero más está puedo".split(" ")),
  pt: new Set("não você com uma os das dos em é ao pelo pela isso também são".split(" ")),
  fr: new Set("le les des et est pour avec vous je pas sur dans qui être".split(" ")),
  it: new Set("il gli che per sono non della delle questo anche più".split(" ")),
  de: new Set("der die das und ist nicht mit für ich sie ein eine zu auf wir".split(" ")),
};
const LOCAL_GREETING: Record<string, string> = { es: "Hola", pt: "Olá", fr: "Bonjour", it: "Ciao", de: "Hallo" };

/** Rough language of a proposal: en, es, pt, fr, it or de. English unless another
 *  language clearly wins, so a few borrowed words change nothing. */
function language(text: string): string {
  const words = (text || "").toLowerCase().match(/[\p{L}\p{N}_]+/gu) ?? [];
  let best = "en";
  let bestScore = -1;
  const scores: Record<string, number> = {};
  for (const [lang, vocab] of Object.entries(LANG_WORDS)) {
    scores[lang] = words.filter((w) => vocab.has(w)).length;
    if (scores[lang] > bestScore) { best = lang; bestScore = scores[lang]; }
  }
  return best !== "en" && bestScore >= 3 && bestScore > scores.en ? best : "en";
}

/** 'Hi,' -> 'Hola,' when `body` is Spanish (and so on). Only an English hi/hello/hey
 *  is swapped, keeping its punctuation; anything else is returned as is. */
function localizeGreeting(greeting: string, body: string): string {
  const m = /^\s*(hi|hello|hey)\b([\s\S]*)$/i.exec(greeting || "");
  const local = m ? LOCAL_GREETING[language(body)] : undefined;
  return m && local ? local + m[2] : greeting;
}

/** The model wrote an English greeting on a non-English proposal (because the
 *  prompt says 'start with Hi,'): 'Hi, el punto ...' -> 'Hola, el punto ...'. */
function localizeBodyGreeting(body: string): string {
  const m = /^\s*((?:hi|hello|hey)\b[,!.:]*)/i.exec(body);
  if (!m) return body;
  const rest = body.slice(m[0].length);
  const local = localizeGreeting(m[1], rest);
  return local !== m[1] ? local + rest : body;
}

/** 'Hi,' + 'Your colonies need ...' -> 'Hi, your colonies need ...'. The old first
 *  word is lower-cased unless it is "I..." / an acronym or a proper noun (the same
 *  capitalised word appears again mid-sentence, e.g. "Shopify"). */
function glueGreeting(greeting: string, text: string): string {
  text = text.trimStart();
  const sp = text.indexOf(" ");
  let first = sp >= 0 ? text.slice(0, sp) : text;
  const sep = sp >= 0 ? " " : "";
  const rest = sp >= 0 ? text.slice(sp + 1) : "";
  const word = first.replace(/[^\p{L}\p{N}_'’]/gu, "");
  const keep = !word || !greeting.trim().endsWith(",") // "...developer." -> new sentence
    // German capitalises nouns and the formal "Sie"/"Ihr": never lower-case there.
    || /^(hallo|guten)\b/i.test(greeting.trim())
    || I_WORDS.has(word.toLowerCase()) || isUpper(word) || capitalisedMidSentence(word, rest);
  if (!keep) first = first[0].toLowerCase() + first.slice(1);
  return `${greeting.trim()} ${first}${sep}${rest}`;
}

/** True when `word` appears capitalised somewhere it isn't a sentence start — the
 *  sign of a name ("... built on Shopify"). "You" opening a later sentence doesn't
 *  count, so it isn't mistaken for one. */
function capitalisedMidSentence(word: string, text: string): boolean {
  for (const m of text.matchAll(new RegExp("\\b" + escapeRe(word) + "\\b", "g"))) {
    const before = text.slice(0, m.index).replace(/[ \t]+$/, "");
    if (before && !".!?:\n\"'“(".includes(before[before.length - 1])) return true;
  }
  return false;
}

/** Lower-case the word after an inline 'Hi,' the model wrote itself
 *  ("Hi, You need ..." -> "Hi, you need ..."). Names and "I" keep their capital. */
function fixGreetingCase(body: string): string {
  const m = new RegExp(String.raw`^((?:${GREETINGS}),)[ \t]+(?=\S)`, "i").exec(body);
  if (!m) return body;
  return glueGreeting(m[1], body.slice(m[0].length));
}

function enforceTemplateFraming(body: string, template: string): string {
  if (!template.trim() || !body.trim()) return body;

  const tpl = template.trim();
  const g = LEAD_GREETING.exec(tpl);
  if (g) {
    // In the proposal's own language: an English template still opens a Spanish
    // proposal with "Hola,".
    const greeting = localizeGreeting(g[0].trim(), body);
    // The template opens "Hi, <sentence>" on ONE line (not "Hi," alone).
    const inline = Boolean(tpl.split("\n", 1)[0].slice(g[0].length).trim());
    const lines = body.split("\n");
    if (inline && GREETING_LINE.test(lines[0].trim())) {
      // The model put the greeting on its own line ("Hi,\n\nYour colonies ..."):
      // join it to the first sentence the way the template does.
      const rest = lines.slice(1);
      while (rest.length && !rest[0].trim()) rest.shift();
      const restText = rest.join("\n");
      if (rest.length) body = glueGreeting(localizeGreeting(lines[0].trim(), restText), restText);
    } else if (!LEAD_GREETING.test(body)) {
      body = glueGreeting(greeting, body);
    }
  }

  const leadIn = templateQuestionLeadIn(template);
  const lines = body.split("\n");
  const qIdx = lines.map((ln, i) => (QUESTION_LINE.test(ln) ? i : -1)).filter((i) => i >= 0);
  if (leadIn && qIdx.length) {
    const wanted = withQuestionCount(leadIn, qIdx.length);
    const firstQ = qIdx[0];
    let prevI: number | null = null;
    for (let i = firstQ - 1; i >= 0; i--) if (lines[i].trim()) { prevI = i; break; }
    const n = (s: string) => s.trim().toLowerCase().replace(/[:.]+$/, "").replace(QUESTION_COUNT, "N questions");
    if (prevI !== null && n(lines[prevI]) === n(leadIn)) lines[prevI] = wanted;
    // The model's own lead-in, e.g. "Tengo dos preguntas:" in a Spanish proposal.
    else if (prevI !== null && lines[prevI].trimEnd().endsWith(":")) { /* keep it */ }
    // Written in another language ("Hola, ..."): an English lead-in would stick out.
    else if (LEAD_GREETING.test(body) && !ENGLISH_GREETING.test(body)) { /* skip it */ }
    else lines.splice(firstQ, 0, wanted);
    body = lines.join("\n");
  }
  return body;
}

function stripLeadingGreeting(body: string, prefix: string): string {
  const n = (s: string) => (s || "").trim().replace(/[\s,!:.-]+$/, "").toLowerCase();
  const greeting = n(prefix);
  if (!greeting || !GREETING_LINE.test(greeting)) return body;
  // GREETING_END, not \b: \b misses accented endings like "olá".
  const m = new RegExp("^" + escapeRe(greeting) + GREETING_END + "[\\s,!:.-]*", "i").exec(body);
  if (!m) return body;
  const rest = body.slice(m[0].length).trimStart();
  if (!rest) return body;
  return rest[0].toUpperCase() + rest.slice(1);
}

function sanitize(text: string): string {
  return text.replace(/\\/g, "").replace(/\n[ \t]*\n[ \t]*\n+/g, "\n\n").trim();
}

function assemble(body: string, o: { lead: string; prefix: string; suffix: string; signature: string; prefixInline: boolean }): string {
  const out: string[] = [];
  if (o.lead.trim()) out.push(`"${o.lead.trim().replace(/^["']+|["']+$/g, "").trim()}"`);
  body = body.trim();
  if (o.prefix.trim()) {
    // "Text at start" = "Hi," becomes "Hola," on a Spanish proposal. Localized first,
    // so the duplicate check below compares like with like.
    const prefix = localizeGreeting(o.prefix, body);
    body = stripLeadingGreeting(body, prefix);
    if (o.prefixInline) {
      body = glueGreeting(prefix, body);
    } else {
      out.push(prefix.trim());
    }
  }
  out.push(body);
  const closing = [o.suffix.trim(), o.signature.trim()].filter(Boolean);
  if (closing.length) out.push(closing.join("\n"));
  return out.join("\n");
}

function jobBlock(j: { title: string; description: string; skills: string; budgetMin: number | null; budgetMax: number | null; currency: string | null }) {
  return `Title: ${j.title}
Skills: ${j.skills || "(none)"}
Budget: ${j.budgetMin}-${j.budgetMax} ${j.currency ?? ""}
Description:
${(j.description || "").slice(0, 4000)}`;
}

/** AI-chosen [amount, days] from the admin's plain-English pricing rules, or null to
 *  fall back to the user's bid rules. Never throws. */
export async function aiPriceAndDuration(
  apiKey: string, model: string, rules: string,
  j: { title: string; description: string; skills: string; budgetMin: number | null; budgetMax: number | null; currency: string | null },
): Promise<[number, number] | null> {
  if (!rules.trim()) return null;
  const system =
    "You set the bid amount and delivery time for a freelancer's auto-bidding bot. " +
    "Use the freelancer's pricing rules and the job details to choose a fair bid. " +
    "Stay within the job's budget range when one is given. " +
    'Reply with ONLY a JSON object: {"amount": <number>, "days": <integer>}.';
  const user = `Freelancer's pricing rules:
${rules.trim()}

Job post:
${jobBlock(j)}

Give the bid amount and delivery days as the JSON object only.`;
  try {
    const obj = extractJson(await ask(new OpenAI({ apiKey }), model, system, user));
    const amount = Number(obj.amount);
    const days = Math.trunc(Number(obj.days));
    if (!(amount > 0) || !(days > 0)) return null;
    return [amount, Math.max(1, days)];
  } catch {
    return null;
  }
}
