from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI


# Generic fallbacks used only when the matching Settings field is blank. They carry
# no personal identity — set BOT_PORTFOLIO_URLS / BOT_PROFILE_BULLETS /
# BOT_PROPOSAL_TEMPLATE / BOT_SIGNATURE_NAME to make proposals your own.
PORTFOLIO_URLS: list[str] = []

DEFAULT_PROFILE_BULLETS = [
    "Senior engineer who has shipped many similar production projects end to end.",
    "Strong across the job's stack — modern web, APIs, mobile, and integrations.",
    "I build clean, modular, well-documented solutions and communicate daily.",
]

# First line of every proposal reply: the client's required start word, or NONE.
LEAD_MARKER = "LEAD:"

# Tone/structure example the model imitates when no custom template is set.
STYLE_EXAMPLE = """Dear Client,
Are you looking for an engineer who can deliver this cleanly and on time?
I am a senior developer with years of experience shipping similar projects end to end.
While many write throwaway scripts, I build modular, maintainable solutions tailored to your stack.
For your project I will map the requirements, implement a reliable first version, then test and hand over with clear notes.
Shall I start with the core feature first?
Best regards,
User"""


@dataclass(frozen=True)
class ProposalInput:
    title: str
    description: str
    budget_min: Optional[float]
    budget_max: Optional[float]
    currency: Optional[str]
    your_profile_bullets: list[str]
    questions: list[str]
    skills: str = ""
    portfolio_urls: list[str] = field(default_factory=lambda: list(PORTFOLIO_URLS))
    signature_name: str = "User"
    extra_instructions: str = ""
    # Personalization toggles / overrides (mirror the settings UI). Empty string
    # template falls back to the built-in STYLE_EXAMPLE.
    include_name: bool = True
    include_profile: bool = True
    ask_question: bool = True
    template: str = ""
    prefix: str = ""
    suffix: str = ""
    # Put the prefix on the SAME line as the first sentence ("Hi, I am a senior...")
    # instead of its own line. See _assemble.
    prefix_inline: bool = False


def _system_rules(*, ask_question: bool, include_profile: bool,
                  has_template: bool, has_portfolio: bool,
                  user_instructions: str) -> str:
    """Build the proposal system prompt.

    HARD RULES (length, plain text, hook, line breaks) apply to every proposal. The
    toggles add/remove the portfolio and question. A user template drives the
    structure, and the user's free-text instructions are the HIGHEST authority — if
    anything conflicts, the model obeys the user. The closing line(s) and signature
    are appended deterministically AFTER generation, so the model never writes them.

    Custom instructions are a WHOLE prompt of the user's own — it may set the length,
    the language, the opening, the layout, the structure and a fixed question block. So
    when they are present EVERY built-in about how the letter reads becomes a DEFAULT
    the user overrides, not a hard rule, and the absolutist wording ('NEVER', 'Do NOT',
    'ask NOTHING') is dropped along with it: stating both sides as absolute left the
    model obeying whichever sat higher in the prompt. That is how a prompt ending in
    'I have two questions. Q1: ... Q2: ...' silently lost its question block (the
    built-in said ask nothing when the post is clear, and Q1/Q2 read as the banned
    numbered list). Only the rules the PIPELINE depends on stay hard in every case: no
    closing salutation or signature, and no verification word (both are appended
    deterministically afterwards), plus 'output the proposal text only'."""
    custom = bool((user_instructions or "").strip())

    def rule(default: str, hard: str) -> str:
        """Pick the wording: a soft default when the user brought their own prompt,
        the strict built-in otherwise."""
        return default if custom else hard

    lines = [
        "You write short, human cover letters (proposals) for Freelancer.com jobs.",
        "Output ONLY the proposal text itself — no preamble, no headings, no markdown, no code fences.",
        "",
        rule("BASE RULES (defaults only — YOUR INSTRUCTIONS at the end override any of these):",
             "HARD RULES (never break these):"),
        rule("- English, unless your instructions ask for another language.",
             "- English only."),
        rule("- Keep it short: your own instructions set the exact length.",
             "- Keep it UNDER 200 words AND under 1200 characters."),
        rule("- Open in a way that shows you understand THIS specific project, following whatever "
             "opening your instructions describe.",
             "- Open with a strong one-line hook that immediately shows you understand THIS specific project."),
        rule("- Plain text: no markdown, no code fences, no backslash (\\) characters. Numbered or "
             "labelled lines (e.g. 'Q1:') where your instructions ask for them.",
             "- Plain text only: NO bullet points, NO numbered lists, NO markdown, NO backslash (\\) characters."),
        rule("- Your instructions decide the layout: line breaks, blank lines, paragraphs.",
             "- Write one sentence per line. Do NOT use empty lines, except at most a single blank line right before the closing."),
        rule("- Confident, natural, human. No fluff, no fake claims.",
             "- Confident, natural, human. No emojis, no fluff, no fake claims. Use ',' never ';'."),
    ]
    if include_profile and has_portfolio:
        lines.append(
            "- A tagged portfolio list follows. Each link shows the tech/role it demonstrates. "
            "Include ONLY the link(s) whose tags best match THIS job's skills and description — "
            "usually 1, at most 2-3. Omit every unrelated link. If none clearly fit, include none. "
            + rule("Place them where your instructions say, and never print the tags or invent links.",
                   "Put each chosen link on its own line, and NEVER print the tags or invent links.")
        )
    elif not include_profile:
        lines.append("- Do NOT include portfolio links or a profile/experience dump.")
    if ask_question and custom:
        # The user's own prompt decides the question policy (how many, what wording,
        # whether a fixed closing block like "I have two questions. Q1: ... Q2: ..." is
        # always present). Repeating the built-in "ask nothing when the post is clear"
        # here would contradict it and, in practice, win.
        lines.append(
            "- Your own instructions below decide whether to ask questions, how many, and in what format. "
            "Follow them exactly, including any fixed wording or Q1/Q2 labels they specify."
        )
    elif ask_question:
        # Questions are earned, not mandatory. Forcing one onto a fully-specified post
        # produces filler the client reads as a template ("what's your ideal deadline?").
        # So: ask only when the post is genuinely ambiguous, otherwise spend the last
        # lines proving you already know how to build it.
        lines.append(
            "- Ask a question ONLY when the post leaves something genuinely unclear that would change HOW you build "
            "it. In that case end with one or two short questions, each drawn from THIS post (their feature, data, "
            "edge case, platform, or integration). NEVER a generic closer about deadlines, timelines, budget, or "
            "when to start."
        )
        lines.append(
            "- When the post is already clear enough to start, ask NOTHING. End instead with a short concrete plan: "
            "the first two or three steps you would actually take on THIS job, one per line, naming their features."
        )
    else:
        lines.append("- Do NOT ask any questions.")
    lines.append(
        "- Do NOT write a closing salutation (no 'Best regards', 'Sincerely', etc.) and do NOT write any name or "
        "signature. End with the last sentence of your pitch or your question — a closing and signature are added "
        "automatically afterwards."
    )
    # Long custom prompts describe a "silent" analysis pass (real goal, friction,
    # client type...). Weaker models print it as the opening ("The real goal is to...").
    lines.append(
        "- Any analysis, extraction or classification step described in the instructions is SILENT: never print "
        "its labels or results (e.g. 'The real goal is', 'Core friction', 'Client type', 'Niche'). Right after "
        "the LEAD line below, the first words are the first words of the proposal itself."
    )
    # One call does both jobs: the model reports the client's anti-AI start word on a
    # machine-readable first line, which the bot strips and places itself.
    lines.append(
        f"- REQUIRED FIRST LINE (read by software and removed before anyone sees it, so it applies even if the "
        f"instructions say to output only the proposal): write '{LEAD_MARKER} <text>' when the job post tells "
        f"bidders to begin their proposal with a specific word, code or phrase (an anti-AI check, e.g. 'start "
        f"your bid with the word Bundle'), copying that text exactly with its casing, and nothing else. "
        f"Otherwise write '{LEAD_MARKER} NONE'. Then start the proposal on the next line, without repeating that "
        f"word — it is placed at the top automatically."
    )

    if has_template:
        lines += [
            "",
            "Follow the STYLE, TONE and STRUCTURE of the user's template below, adapting its wording to THIS job. "
            "Rewrite its job-specific content for this post, but KEEP whatever fixed framing lines the template "
            "itself has (its greeting, any lead-in sentence before a question block, and the like), only "
            "adjusting a number in them to match what you actually write. If the template has no such line, do "
            "not add one. Drop only content sentences that do not apply to this job. Where the template and the "
            "USER INSTRUCTIONS disagree, the instructions win.",
        ]
    elif not custom:
        # A built-in structure would fight a custom prompt that lays out its own
        # sections (opening, approach, questions), so it is only offered when the user
        # has given neither a template nor instructions of their own.
        lines += [
            "",
            "Structure: the hook, then 1-2 sentences on your directly-relevant experience with this job's exact stack, "
            "then a brief concrete approach written as flowing sentences (NOT a list), then the closing.",
        ]

    if (user_instructions or "").strip():
        lines += [
            "",
            "USER INSTRUCTIONS (HIGHEST priority — if anything above conflicts with these, obey these):",
            user_instructions.strip(),
        ]
    return "\n".join(lines)


def _split_portfolio_entry(entry: str) -> tuple[str, str]:
    """Split a portfolio entry into ``(url, tags)``. Tags describe the tech/role the
    link demonstrates and steer which link the AI picks per job; they are never shown.

    Accepts ``URL | tags`` (preferred) and, as a convenience, ``URL<tab/2+ spaces>tags``
    (so a list pasted straight from a spreadsheet works). A bare URL yields empty tags.
    """
    e = (entry or "").strip()
    if "|" in e:
        url, tags = e.split("|", 1)
        return url.strip(), tags.strip()
    m = re.match(r"(\S+)(?:\t+|\s{2,})(.+)", e)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return e, ""


def _split_lead_line(text: str) -> tuple[str, str]:
    """Split the model's reply into ``(lead, proposal)``.

    Some clients hide an anti-AI check in the post ("start your bid with the word
    Bundle"). The proposal call reports that text on its first line as
    ``LEAD: <text>`` (or ``LEAD: NONE``) so no second OpenAI call is needed to find
    it. A reply without the line is treated as having no lead, so a model that
    skips it still yields a normal proposal."""
    lines = (text or "").strip().split("\n")
    pattern = re.compile(r"^\W*" + re.escape(LEAD_MARKER.rstrip(":")) + r"\W*:\s*(.*)$", re.IGNORECASE)
    # Usually the first line, but a model that put it lower must still never leak
    # "LEAD: NONE" into the bid the client reads.
    for i, ln in enumerate(lines):
        m = pattern.match(ln.strip())
        if m:
            lead = m.group(1).strip(" \t\"'“”‘’*`")
            if lead.upper() in {"NONE", "N/A", "NA", "NULL", "-", ""}:
                lead = ""
            return lead, "\n".join(lines[:i] + lines[i + 1:]).strip()
    return "", (text or "").strip()


def generate_proposal_openai(api_key: str, model: str, data: ProposalInput) -> str:
    client = OpenAI(api_key=api_key)

    portfolio = [u for u in (data.portfolio_urls or []) if str(u).strip()]
    bullets = data.your_profile_bullets or DEFAULT_PROFILE_BULLETS
    template = (data.template or "").strip()
    signature = (data.signature_name or "").strip()
    has_name = bool(signature)
    has_portfolio = bool(portfolio)

    blocks = [
        f"Job title: {data.title}",
        "",
        "Job description:",
        data.description or "(none)",
        "",
        f"Required skills (weave the relevant ones in naturally): {data.skills or '(none listed)'}",
        f"Budget: {data.budget_min}-{data.budget_max} {data.currency}",
    ]
    if data.include_profile:
        blocks += [
            "",
            # Headed "MY FACTS" because that is the name custom instructions refer to
            # ("use only what MY FACTS gives you"). A prompt that cites a section the
            # model can't find has nothing to write its credibility lines from.
            "MY FACTS — true facts about you (draw on these, do NOT invent others, do NOT list them verbatim):",
            "\n".join(f"- {b}" for b in bullets),
        ]
        if has_portfolio:
            parsed = [_split_portfolio_entry(u) for u in portfolio]
            if any(tags for _, tags in parsed):
                rendered = "\n".join(
                    f"- {url}  (demonstrates: {tags})" if tags else f"- {url}"
                    for url, tags in parsed
                )
                header = (
                    "Portfolio links with the tech/role each one demonstrates. Pick ONLY the "
                    "link(s) whose 'demonstrates' tags match this job; omit the rest; never print "
                    "the tags:"
                )
            else:
                rendered = "\n".join(portfolio)
                header = "Portfolio URLs you may reference (only these, each on its own line):"
            blocks += ["", header, rendered]
    if data.ask_question and data.questions:
        blocks += [
            "",
            "You may adapt ONE of these as your closing question (keep it short):",
            "\n".join(f"- {q}" for q in data.questions),
        ]
    if template:
        blocks += ["", "USER TEMPLATE to follow (adapt its wording to this job):", template]
    # The reminder sits last because the end of the prompt is what models follow most.
    blocks += ["", f"Write the proposal now, starting with the '{LEAD_MARKER}' line."]
    user_prompt = "\n".join(blocks)

    # Responses API (recommended for new builds).
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": _system_rules(
                ask_question=data.ask_question,
                include_profile=data.include_profile,
                has_template=bool(template),
                has_portfolio=has_portfolio,
                user_instructions=data.extra_instructions,
            )},
            {"role": "user", "content": user_prompt},
        ],
    )
    # The SDK returns a structured response; simplest is `output_text`. Its first
    # line carries the client-required start word (or NONE), not proposal text.
    lead, raw = _split_lead_line(resp.output_text)
    body = _sanitize(raw)
    # Same switch, other source: a greeting the model wrote itself (because the
    # user's instructions asked for one) also belongs on the first sentence's line.
    if data.prefix_inline:
        body = _merge_greeting_line(body)
    # Models drop the template's fixed lines now and then (the greeting, the
    # "I have two questions:" lead-in) — restore whatever THIS template has.
    body = _enforce_template_framing(body, template)
    # "Hi, You need ..." -> "Hi, you need ..." however the greeting got there.
    body = _fix_greeting_case(_localize_body_greeting(body))
    # Client-required verification word (anti-AI check) goes ABOVE everything else.
    return _assemble(
        _strip_lead_from_body(body, lead),
        lead=lead,
        prefix=data.prefix,
        suffix=data.suffix,
        signature=signature if data.include_name else "",
        prefix_inline=data.prefix_inline,
    )


def _strip_lead_from_body(body: str, lead: str) -> str:
    """Drop the client's required opening phrase when the model wrote it anyway.

    The system prompt tells the model to leave the verification word alone because
    :func:`_assemble` prepends it — but the JOB POST tells the model to start with it,
    and the post usually wins. That produced proposals opening with the phrase twice
    ('"ARCHITECTURE FIRST"' then 'ARCHITECTURE FIRST'), which looks worse than either
    choice alone. Compares loosely (case, quotes and punctuation ignored) so 'Bundle.'
    and '"bundle"' are both recognised as the same line."""
    if not (lead or "").strip():
        return body
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    target = norm(lead)
    if not target:
        return body
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and norm(lines[0]) == target:
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(lines).strip()
    return body


# Greetings the bot recognises, in the languages proposals get written in (a
# Spanish post gets "Hola,"). Without them a non-English greeting looked missing
# and "Hi," was added in front of it.
_GREETINGS = r"hi|hello|hey|good (?:morning|afternoon|evening)|hola|buen(?:os|as) (?:d[ií]as|tardes|noches)|buenas|bonjour|salut|hallo|guten (?:tag|morgen|abend)|ol[aá]|oi|bom dia|boa (?:tarde|noite)|ciao|salve|buongiorno|hej|hei|hoi|merhaba|namaste"
# End of a greeting word; \b misses accented endings like "olá".
_GREETING_END = r"(?=[\s,!.:]|$)"

_GREETING_LINE = re.compile(
    r"^(" + _GREETINGS + r"|dear [a-z .'-]{0,24})[,!:.]*$",
    re.IGNORECASE,
)


def _merge_greeting_line(text: str) -> str:
    """Join a greeting the MODEL wrote on its own first line to the sentence below it.

    The "one sentence per line" rule makes the model put a greeting on its own line,
    so an instruction like "start the proposal with Hi," yields::

        Hi,
        I am a senior developer ...

    With the inline option on, the user wants ``Hi, I am a senior developer ...``.
    Only a bare greeting line is merged — a first line that carries real content is
    never touched."""
    lines = text.split("\n")
    if len(lines) < 2 or not _GREETING_LINE.match(lines[0].strip()):
        return text
    rest = lines[1:]
    while rest and not rest[0].strip():  # greeting followed by a blank line
        rest.pop(0)
    if not rest:
        return text
    return _glue_greeting(lines[0].strip(), "\n".join(rest))


_LEAD_GREETING = re.compile(
    r"^\s*(" + _GREETINGS + r")" + _GREETING_END + r"[,!.:]*",
    re.IGNORECASE,
)
_ENGLISH_GREETING = re.compile(r"^\s*(hi|hello|hey|good (?:morning|afternoon|evening))\b", re.IGNORECASE)
_QUESTION_LINE = re.compile(r"^\s*Q\d+\s*[:.)]", re.IGNORECASE)
_QUESTION_COUNT = re.compile(r"\b(one|two|three|four|five|\d+)(\s+)questions?\b", re.IGNORECASE)
_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
_I_WORDS = {"i", "i'm", "i’m", "i'd", "i’d", "i've", "i’ve", "i'll", "i’ll"}


def _template_question_lead_in(template: str) -> str:
    """The template's line right before its first 'Q1:' line (e.g. 'I have two
    questions:'), or '' when the template has no question block or no lead-in."""
    lines = [ln.strip() for ln in (template or "").splitlines()]
    for i, ln in enumerate(lines):
        if _QUESTION_LINE.match(ln):
            prev = next((p for p in reversed(lines[:i]) if p), "")
            # Only a real lead-in ("I have two questions:"), not a content sentence
            # that happens to sit above Q1.
            is_lead_in = prev.endswith(":") or "question" in prev.lower()
            return prev if is_lead_in and not _QUESTION_LINE.match(prev) else ""
    return ""


def _with_question_count(lead_in: str, n: int) -> str:
    """'I have two questions:' -> 'I have one question:' when n == 1, etc."""
    word = _COUNT_WORDS.get(n, str(n))

    def repl(m: re.Match) -> str:
        w = word.capitalize() if m.group(1)[0].isupper() else word
        return f"{w}{m.group(2)}{'question' if n == 1 else 'questions'}"

    return _QUESTION_COUNT.sub(repl, lead_in, count=1)


# A post in another language gets a proposal in that language, and an English "Hi,"
# on top of it reads as a slip. These words identify the language (each list holds
# words common in that language and rare in the others), and the map gives the
# greeting to use instead.
_LANG_WORDS = {
    "en": set("the and to of is for with you your this that will we it be on are".split()),
    "es": set("el los las del que y para con por una sin es lo como pero más está puedo".split()),
    "pt": set("não você com uma os das dos em é ao pelo pela isso também são".split()),
    "fr": set("le les des et est pour avec vous je pas sur dans qui être".split()),
    "it": set("il gli che per sono non della delle questo anche più".split()),
    "de": set("der die das und ist nicht mit für ich sie ein eine zu auf wir".split()),
}
_LOCAL_GREETING = {"es": "Hola", "pt": "Olá", "fr": "Bonjour", "it": "Ciao", "de": "Hallo"}


def _language(text: str) -> str:
    """Rough language of a proposal: 'en', 'es', 'pt', 'fr', 'it' or 'de'. English
    unless another language clearly wins, so a few borrowed words change nothing."""
    words = re.findall(r"\w+", (text or "").lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in _LANG_WORDS.items()}
    best = max(scores, key=scores.get)
    if best != "en" and scores[best] >= 3 and scores[best] > scores["en"]:
        return best
    return "en"


def _localize_greeting(greeting: str, body: str) -> str:
    """'Hi,' -> 'Hola,' when ``body`` is Spanish (and so on). Only an English
    hi/hello/hey is swapped, keeping its punctuation; anything else is returned as is."""
    m = re.match(r"^\s*(hi|hello|hey)\b(.*)$", greeting or "", re.IGNORECASE | re.DOTALL)
    local = _LOCAL_GREETING.get(_language(body)) if m else None
    return f"{local}{m.group(2)}" if local else greeting


def _localize_body_greeting(body: str) -> str:
    """The model wrote an English greeting on a non-English proposal (because the
    prompt says 'start with Hi,'): 'Hi, el punto ...' -> 'Hola, el punto ...'."""
    m = re.match(r"^\s*((?:hi|hello|hey)\b[,!.:]*)", body, re.IGNORECASE)
    if not m:
        return body
    local = _localize_greeting(m.group(1), body[m.end():])
    return local + body[m.end():] if local != m.group(1) else body


def _glue_greeting(greeting: str, text: str) -> str:
    """'Hi,' + 'Your colonies need ...' -> 'Hi, your colonies need ...'.

    The old first word is lower-cased unless it is "I..." / an acronym or a proper
    noun (the same capitalised word appears again later, e.g. "Shopify")."""
    text = text.lstrip()
    first, sep, rest = text.partition(" ")
    word = re.sub(r"[^\w'’]", "", first)
    keep = (not word or not greeting.strip().endswith(",")  # "...developer." -> new sentence
            # German capitalises nouns and the formal "Sie"/"Ihr": never lower-case there.
            or re.match(r"(hallo|guten)\b", greeting.strip(), re.IGNORECASE) is not None
            or word.lower() in _I_WORDS or word.isupper()
            or _capitalised_mid_sentence(word, rest))
    if not keep:
        first = first[0].lower() + first[1:]
    return f"{greeting.strip()} {first}{sep}{rest}"


def _capitalised_mid_sentence(word: str, text: str) -> bool:
    """True when ``word`` appears capitalised somewhere it isn't a sentence start —
    the sign of a name ("... built on Shopify"). "You" opening a later sentence
    doesn't count, so it isn't mistaken for one."""
    for m in re.finditer(r"\b" + re.escape(word) + r"\b", text):
        before = text[:m.start()].rstrip(" \t")
        if before and before[-1] not in ".!?:\n\"'“(":
            return True
    return False


def _fix_greeting_case(body: str) -> str:
    """Lower-case the word after an inline 'Hi,' the model wrote itself
    ("Hi, You need ..." -> "Hi, you need ..."). Names and "I" keep their capital."""
    m = re.match(r"((?:" + _GREETINGS + r"),)[ \t]+(?=\S)", body, re.IGNORECASE)
    if not m:
        return body
    return _glue_greeting(m.group(1), body[m.end():])


def _enforce_template_framing(body: str, template: str) -> str:
    """Restore the fixed framing lines of the user's OWN template when the model
    dropped them: its opening greeting and the lead-in line before its Q1/Q2 block.

    Everything is derived from the template, nothing is hard-coded — a template
    without a greeting or lead-in gets neither. The lead-in's question count is
    adjusted to the number of Q-lines actually written."""
    if not (template or "").strip() or not body.strip():
        return body

    # 1) Opening greeting ("Hi," in "Hi, I'd trace the flow ...").
    g = _LEAD_GREETING.match(template.strip())
    if g:
        # In the proposal's own language: an English template still opens a Spanish
        # proposal with "Hola,".
        greeting = _localize_greeting(g.group(0).strip(), body)
        # The template opens "Hi, <sentence>" on ONE line (not "Hi," alone).
        inline = bool(template.strip().split("\n", 1)[0][g.end():].strip())
        lines = body.split("\n")
        if inline and _GREETING_LINE.match(lines[0].strip()):
            # The model put the greeting on its own line ("Hi,\n\nYour colonies ..."):
            # join it to the first sentence the way the template does.
            rest = lines[1:]
            while rest and not rest[0].strip():
                rest.pop(0)
            if rest:
                rest_text = "\n".join(rest)
                body = _glue_greeting(_localize_greeting(lines[0].strip(), rest_text), rest_text)
        elif not _LEAD_GREETING.match(body):
            body = _glue_greeting(greeting, body)

    # 2) Lead-in line before the questions ("I have two questions:").
    lead_in = _template_question_lead_in(template)
    lines = body.split("\n")
    q_idx = [i for i, ln in enumerate(lines) if _QUESTION_LINE.match(ln)]
    if lead_in and q_idx:
        wanted = _with_question_count(lead_in, len(q_idx))
        first_q = q_idx[0]
        prev_i = next((i for i in range(first_q - 1, -1, -1) if lines[i].strip()), None)
        norm = lambda s: _QUESTION_COUNT.sub("N questions", s.strip().lower().rstrip(":."))
        if prev_i is not None and norm(lines[prev_i]) == norm(lead_in):
            lines[prev_i] = wanted  # present — just fix the count
        elif prev_i is not None and lines[prev_i].rstrip().endswith(":"):
            pass  # the model's own lead-in, e.g. "Tengo dos preguntas:" in a Spanish proposal
        elif _LEAD_GREETING.match(body) and not _ENGLISH_GREETING.match(body):
            pass  # written in another language ("Hola, ..."): an English lead-in would stick out
        else:
            lines.insert(first_q, wanted)
        body = "\n".join(lines)
    return body


def _strip_leading_greeting(body: str, prefix: str) -> str:
    """Drop the model's own opening greeting when the "Text at start" prefix is that
    same greeting. A custom prompt or template that says "start with Hi," makes the
    model write it too, which would otherwise read "Hi, Hi, ..." once the prefix is
    added. Only an exact greeting match (case and trailing punctuation ignored) is
    removed, so a prefix like "I am a senior developer" never eats real content."""
    norm = lambda s: re.sub(r"[\s,!:.\-]+$", "", (s or "").strip()).lower()
    greeting = norm(prefix)
    if not greeting or not _GREETING_LINE.match(greeting):
        return body
    m = re.match(re.escape(greeting) + r"\b[\s,!:.\-]*", body, re.IGNORECASE)
    if not m:
        return body
    rest = body[m.end():].lstrip()
    if not rest:
        return body
    return rest[0].upper() + rest[1:]


def _sanitize(text: str) -> str:
    """Enforce two of the formatting rules deterministically (models still slip):
    drop backslash characters, and collapse runs of blank lines to at most one."""
    text = text.replace("\\", "")
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)  # 2+ blank lines -> 1
    return text.strip()


def _assemble(body: str, *, lead: str = "", prefix: str, suffix: str, signature: str,
              prefix_inline: bool = False) -> str:
    """Build the final letter: [lead] -> [prefix] -> body -> [closing suffix] -> [name].

    ``lead`` is a client-required verification word/phrase (an anti-AI check pulled
    from the job post) that MUST be the absolute first line — above the "Text at
    start" prefix — so the client's checker sees it immediately::

        Bundle                                            <- lead (required word)
        I am a senior full stack developer ...            <- prefix ("Text at start")
        ...your pitch...

    The signature name is always LAST so the ending reads, e.g.::

        ...your pitch...
        Hope to dive into your project asap.
        Thank you.
        Anoosher

    where the two closing lines come from ``suffix`` (Text at end) and ``Anoosher``
    from the signature. The model never writes the lead/closing/name itself."""
    out = []
    if (lead or "").strip():
        # Present the required word quoted, e.g. "Bundle" (strip any quotes the
        # detector already captured so we never double-wrap).
        out.append(f'"{lead.strip().strip(chr(34)).strip(chr(39)).strip()}"')
    body = body.strip()
    if (prefix or "").strip():
        # "Text at start" = "Hi," becomes "Hola," on a Spanish proposal. Localized
        # first, so the duplicate check below compares like with like.
        prefix = _localize_greeting(prefix, body)
        body = _strip_leading_greeting(body, prefix)
        if prefix_inline:
            # Greeting glued to the first sentence: "Hi, I am a senior ...". The model
            # writes one sentence per line, so a prefix appended as its own element
            # always lands on its own line — joining here is the only deterministic
            # way to get the inline form. Whatever punctuation the prefix ends with
            # ("Hi," / "Hello -") is kept; the two are joined by a single space.
            body = _glue_greeting(prefix, body)
        else:
            out.append(prefix.strip())
    out.append(body)
    closing = []
    if (suffix or "").strip():
        closing.append(suffix.strip())
    if (signature or "").strip():
        closing.append(signature.strip())
    if closing:
        out.append("\n".join(closing))
    return "\n".join(out)


def build_proposal_input(s, *, title: str, description: str, skills: str,
                         budget_min, budget_max, currency, questions: list[str]) -> ProposalInput:
    """Assemble a ProposalInput from a Settings object + job fields, applying the
    user's personalization (identity, toggles, template, prefix/suffix). Empty
    identity values fall back to the built-in defaults inside the generator."""
    return ProposalInput(
        title=title,
        description=description,
        budget_min=budget_min,
        budget_max=budget_max,
        currency=currency,
        your_profile_bullets=s.profile_bullets,
        questions=questions,
        skills=skills,
        portfolio_urls=s.portfolio_urls,
        signature_name=s.signature_name,
        extra_instructions=s.proposal_instructions,
        include_name=s.include_name,
        include_profile=s.include_profile,
        ask_question=s.ask_question,
        template=s.proposal_template,
        prefix=s.proposal_prefix,
        suffix=s.proposal_suffix,
        prefix_inline=s.proposal_prefix_inline,
    )


def _extract_json(text: str) -> dict:
    """Best-effort pull a JSON object out of a model reply (handles ```json fences
    and surrounding prose). Returns {} when nothing parseable is found."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _job_block(title: str, description: str, skills: str,
               budget_min: Optional[float], budget_max: Optional[float],
               currency: Optional[str]) -> str:
    return f"""Title: {title}
Skills: {skills or "(none)"}
Budget: {budget_min}-{budget_max} {currency or ""}
Description:
{(description or "")[:4000]}"""


def ai_filter_project(
    api_key: str,
    model: str,
    criteria: str,
    *,
    title: str,
    description: str,
    skills: str = "",
    budget_min: Optional[float] = None,
    budget_max: Optional[float] = None,
    currency: Optional[str] = None,
) -> tuple[bool, str]:
    """LLM gate: should we bid on this job given the user's free-text criteria?

    Returns ``(should_bid, reason)``. Fails OPEN — on any API/parse error it
    returns ``(True, "ai_filter_error:...")`` so an outage never silently drops
    every job. An empty criteria string also passes everything through."""
    if not (criteria or "").strip():
        return True, "ai_filter:no_criteria"
    system = (
        "You are a strict screening filter for a freelancer's auto-bidding bot. "
        "Given the freelancer's criteria and a job post, decide whether they should bid. "
        'Reply with ONLY a JSON object: {"bid": true|false, "reason": "<short reason>"}.'
    )
    user = f"""Freelancer's bidding criteria:
{criteria.strip()}

Job post:
{_job_block(title, description, skills, budget_min, budget_max, currency)}

Should they bid? Respond with the JSON object only."""
    try:
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        obj = _extract_json(resp.output_text.strip())
        if "bid" not in obj:
            return True, "ai_filter_error:unparseable"
        should = bool(obj.get("bid"))
        reason = str(obj.get("reason") or "")[:200]
        return should, f"ai_filter:{'accept' if should else 'reject'}:{reason}"
    except Exception as exc:  # noqa: BLE001 - never let the filter crash the poll
        return True, f"ai_filter_error:{type(exc).__name__}"


def ai_price_and_duration(
    api_key: str,
    model: str,
    rules: str,
    *,
    title: str,
    description: str,
    skills: str = "",
    budget_min: Optional[float] = None,
    budget_max: Optional[float] = None,
    currency: Optional[str] = None,
) -> Optional[tuple[float, int]]:
    """LLM-derived ``(amount, delivery_days)`` from the user's natural-language
    pricing rules (e.g. "Logo = $50, 2 days"). Returns ``None`` when rules are
    empty, the reply is unparseable, or the call errors, so the caller falls back
    to its structured BID_RULES / budget heuristic."""
    if not (rules or "").strip():
        return None
    system = (
        "You set the bid amount and delivery time for a freelancer's auto-bidding bot. "
        "Use the freelancer's pricing rules and the job details to choose a fair bid. "
        "Stay within the job's budget range when one is given. "
        'Reply with ONLY a JSON object: {"amount": <number>, "days": <integer>}.'
    )
    user = f"""Freelancer's pricing rules:
{rules.strip()}

Job post:
{_job_block(title, description, skills, budget_min, budget_max, currency)}

Give the bid amount and delivery days as the JSON object only."""
    try:
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        obj = _extract_json(resp.output_text.strip())
        amount = float(obj.get("amount"))
        days = int(float(obj.get("days")))
        if amount <= 0 or days <= 0:
            return None
        return amount, max(1, days)
    except (ValueError, TypeError, KeyError):
        return None
    except Exception:  # noqa: BLE001 - pricing failure should fall back, not crash
        return None
