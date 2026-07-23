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


def _system_rules(*, ask_question: bool, include_profile: bool,
                  has_template: bool, has_portfolio: bool,
                  user_instructions: str) -> str:
    """Build the proposal system prompt.

    HARD RULES (length, plain text, hook, line breaks) apply to every proposal. The
    toggles add/remove the portfolio and question. A user template drives the
    structure, and the user's free-text instructions are the HIGHEST authority — if
    anything conflicts, the model obeys the user. The closing line(s) and signature
    are appended deterministically AFTER generation, so the model never writes them."""
    lines = [
        "You write short, human cover letters (proposals) for Freelancer.com jobs.",
        "Output ONLY the proposal text itself — no preamble, no headings, no markdown, no code fences.",
        "",
        "HARD RULES (never break these):",
        "- English only.",
        "- Keep it UNDER 200 words AND under 1200 characters.",
        "- Open with a strong one-line hook that immediately shows you understand THIS specific project.",
        "- Plain text only: NO bullet points, NO numbered lists, NO markdown, NO backslash (\\) characters.",
        "- Write one sentence per line. Do NOT use empty lines, except at most a single blank line right before the closing.",
        "- Confident, natural, human. No emojis, no fluff, no fake claims. Use ',' never ';'.",
    ]
    if include_profile and has_portfolio:
        lines.append(
            "- A tagged portfolio list follows. Each link shows the tech/role it demonstrates. "
            "Include ONLY the link(s) whose tags best match THIS job's skills and description — "
            "usually 1, at most 2-3. Omit every unrelated link. If none clearly fit, include none. "
            "Put each chosen link on its own line, and NEVER print the tags or invent links."
        )
    elif not include_profile:
        lines.append("- Do NOT include portfolio links or a profile/experience dump.")
    if ask_question:
        lines.append("- End with exactly ONE short, specific question to the client.")
    else:
        lines.append("- Do NOT ask any questions.")
    lines.append(
        "- Do NOT write a closing salutation (no 'Best regards', 'Sincerely', etc.) and do NOT write any name or "
        "signature. End with the last sentence of your pitch or your question — a closing and signature are added "
        "automatically afterwards."
    )
    lines.append(
        "- If the job post asks bidders to begin with a specific verification word, code, or phrase (an anti-AI "
        "check, e.g. 'start with the word Bundle'), do NOT write it yourself — it is prepended automatically as the "
        "very first line. Just start with your normal hook."
    )

    if has_template:
        lines += [
            "",
            "Follow the STYLE, TONE and STRUCTURE of the user's template below, adapting its wording to THIS job. "
            "Do not copy it word-for-word, and drop any of its lines that are irrelevant to this job.",
        ]
    else:
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


def detect_required_lead(api_key: str, model: str, description: str) -> str:
    """Pull a client's hidden anti-AI instruction out of the job post.

    Some clients embed a check to catch copy-pasted / AI proposals: they ask the
    bidder to begin their proposal with a specific exact word, code, or phrase
    (e.g. "start your bid with the word Bundle", "type GREEN at the very top").
    This returns that exact text (verbatim, original casing) so it can be placed
    as the first line, or ``""`` when the post demands no such thing.

    Fails safe: any error, empty description, or unparseable reply returns ``""``
    so proposal generation is never blocked."""
    if not (description or "").strip():
        return ""
    system = (
        "You scan a Freelancer.com job post for a hidden anti-AI instruction that "
        "tells bidders to BEGIN their proposal with a specific exact word, code, or "
        "phrase (e.g. 'start your bid with the word Bundle', 'type GREEN at the top', "
        "'begin your proposal with ...'). "
        'Reply with ONLY a JSON object: {"lead": "<exact text to put first, or empty string>"}. '
        "Copy the required text verbatim, preserving its exact casing and punctuation. "
        "Return an empty string if the post does not demand a specific starting word/phrase. "
        "Do NOT invent one and do NOT include any surrounding words."
    )
    user = f"Job post:\n{(description or '')[:4000]}\n\nReturn the JSON object only."
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
        return str(obj.get("lead") or "").strip()
    except Exception:  # noqa: BLE001 - detection must never block the bid
        return ""


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
            "Facts about you (draw on these, do NOT invent others, do NOT list them verbatim):",
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
    blocks += ["", "Write the proposal now."]
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
    # The SDK returns a structured response; simplest is `output_text`.
    body = _sanitize(resp.output_text.strip())
    # Client-required verification word (anti-AI check) goes ABOVE everything else.
    lead = detect_required_lead(api_key, model, data.description)
    return _assemble(
        body,
        lead=lead,
        prefix=data.prefix,
        suffix=data.suffix,
        signature=signature if data.include_name else "",
    )


def _sanitize(text: str) -> str:
    """Enforce two of the formatting rules deterministically (models still slip):
    drop backslash characters, and collapse runs of blank lines to at most one."""
    text = text.replace("\\", "")
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)  # 2+ blank lines -> 1
    return text.strip()


def _assemble(body: str, *, lead: str = "", prefix: str, suffix: str, signature: str) -> str:
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
    if (prefix or "").strip():
        out.append(prefix.strip())
    out.append(body.strip())
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
