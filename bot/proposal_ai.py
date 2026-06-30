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


def _system_rules(*, ask_question: bool, include_name: bool, include_profile: bool) -> str:
    """Build the proposal system prompt, toggling parts the user disabled.

    The three checkboxes from the FABB-style AI config change the strict format:
    whether a question is asked, whether the name is signed, and whether the
    profile/experience is described."""
    parts = ["1) Skills/experience: state you have built similar projects before. Use the REAL technical keywords from this job, and say briefly how you approached the solution."]
    if include_profile:
        parts.append("2) Portfolio: reference ONLY the provided portfolio URLs. For the 2-4 most relevant ones, give a one-line note of what solution you built there and how you approached it, tied to this job's tech. Never invent links.")
    parts.append("3) Plan: a concrete step-by-step approach for THIS specific job, using its tools.")
    content_block = "\n".join(parts)

    if ask_question:
        question_rules = (
            "- First line: a short greeting. Right after it, open with ONE short question.\n"
            "- Before the end: ONE short question, then a closing greeting"
        )
        desc_questions = "If the job description contains questions, answer each with a real, simple, natural spoken answer of 1-2 sentences — the way you would actually say it.\n\n"
    else:
        question_rules = (
            "- First line: a short greeting, then go straight into the value you bring. Do NOT ask any questions anywhere.\n"
            "- Before the end: a closing greeting"
        )
        desc_questions = ""

    name_rule = ", then the name on the last line." if include_name else "."

    return f"""You write short, humanized cover letters (proposals) for Freelancer.com jobs.

Voice: a senior engineer (10+ years) who has already shipped SIMILAR projects. Confident, natural, human. No emojis, no fluff, no fake claims.

Content — in this order:
{content_block}

{desc_questions}Formatting rules (STRICT):
- English only.
{question_rules}{name_rule}
- Put each sentence on its own line. Do NOT add empty lines anywhere.
- Use "," never ";".
- Humanized and concise. Total length MUST be under 1200 characters.
"""


def generate_proposal_openai(api_key: str, model: str, data: ProposalInput) -> str:
    client = OpenAI(api_key=api_key)

    portfolio = data.portfolio_urls or PORTFOLIO_URLS
    bullets = data.your_profile_bullets or DEFAULT_PROFILE_BULLETS
    style_example = (data.template or "").strip() or STYLE_EXAMPLE
    signature = (data.signature_name or "").strip() or "User"

    portfolio_block = "\n".join(f"- {u}" for u in portfolio)
    profile_block = "\n- ".join(bullets)
    suggested_questions = "\n- ".join(data.questions) if data.questions else "(none)"
    # User-supplied steering from the settings UI. Marked HIGH PRIORITY so the model
    # weights it above the generic guidance, but the STRICT format rules still win.
    extra_block = (
        f"\nADDITIONAL INSTRUCTIONS (high priority, obey unless they break the strict format rules):\n{data.extra_instructions.strip()}\n"
        if (data.extra_instructions or "").strip()
        else ""
    )

    profile_prompt = (
        f"""My proof bullets (use these, do NOT invent new ones):
- {profile_block}

My portfolio URLs (reference only these, pick the most relevant):
{portfolio_block}
"""
        if data.include_profile
        else ""
    )
    questions_prompt = (
        f"""Suggested questions you may ask (choose/rewrite, keep them short):
- {suggested_questions}
"""
        if data.ask_question
        else ""
    )
    sign_prompt = f"Sign the letter as: {signature}\n" if data.include_name else ""

    user_prompt = f"""Job title: {data.title}

Job description:
{data.description}

Required skills (weave the relevant ones into the proposal naturally): {data.skills or "(none listed)"}

Budget: {data.budget_min}-{data.budget_max} {data.currency}

{profile_prompt}{questions_prompt}{sign_prompt}
Reference style (for TONE and structure only — follow the STRICT formatting rules above, not this example's spacing):
{style_example}
{extra_block}
Write the proposal now."""

    # Responses API (recommended for new builds).
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": _system_rules(
                ask_question=data.ask_question,
                include_name=data.include_name,
                include_profile=data.include_profile,
            )},
            {"role": "user", "content": user_prompt},
        ],
    )
    # The SDK returns a structured response; simplest is `output_text`.
    body = resp.output_text.strip()
    return _wrap(body, data.prefix, data.suffix)


def _wrap(body: str, prefix: str, suffix: str) -> str:
    """Prepend/append the user's literal 'text at start' / 'text at end'."""
    out = []
    if (prefix or "").strip():
        out.append(prefix.strip())
    out.append(body)
    if (suffix or "").strip():
        out.append(suffix.strip())
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
