from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

# Oleksandr's real portfolio — the model may only reference these links, never invent new ones.
PORTFOLIO_URLS = [
    "https://vr-arena-16303.web.app/en",
    "https://agrinp.com/",
    "https://hearmoremedical.com/",
    "https://hartwell.no/",
    "https://www.tripshock.com/",
    "https://verholy.com/",
]

DEFAULT_PROFILE_BULLETS = [
    "Senior engineer, 10+ years, who has shipped many similar production projects end to end.",
    "Strong in the job's stack — serverless/Firebase, Node.js, APIs, plus modern web (React/Next.js) and mobile.",
    "I build modular, environment-driven, well-documented solutions and communicate daily.",
]

# Oleksandr's own template — used as a style/tone reference for the model.
STYLE_EXAMPLE = """Dear Client,
Are you looking for a Firebase expert who can cleanly connect your JavaScript Cloud Functions to SendGrid or Mailgun for automated transactional and promotional emails?
I am a Senior Backend Engineer with over 10 years specializing in serverless architectures, Firebase Extensions, Node.js, and secure API integrations.
While many write hard-coded scripts, I build modular, environment-driven Cloud Functions that pull dynamic Firestore data.
On vr-arena-16303.web.app I wired Firestore-triggered Cloud Functions for real-time updates, and on tripshock.com I integrated third-party booking APIs with secure key handling.
For your project I will configure a reusable Node.js Cloud Function triggered by Firestore writes or Auth events for both email types.
I will integrate SendGrid or Mailgun via official SDKs, keeping API keys in Google Cloud Secret Manager.
Then I will build dynamic HTML templates that inject user-specific Firestore data, with a short README for deployment.
Shall I start with the transactional flow first?
Best regards,
Oleksandr"""


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
    signature_name: str = "Oleksandr"


SYSTEM_RULES = """You write short, humanized cover letters (proposals) for Freelancer.com jobs.

Voice: a senior engineer (10+ years) who has already shipped SIMILAR projects. Confident, natural, human. No emojis, no fluff, no fake claims.

Content — exactly three parts, in this order:
1) Skills/experience: state you have built similar projects before. Use the REAL technical keywords from this job, and say briefly how you approached the solution.
2) Portfolio: reference ONLY the provided portfolio URLs. For the 2-4 most relevant ones, give a one-line note of what solution you built there and how you approached it, tied to this job's tech. Never invent links.
3) Plan: a concrete step-by-step approach for THIS specific job, using its tools.

If the job description contains questions, answer each with a real, simple, natural spoken answer of 1-2 sentences — the way you would actually say it.

Formatting rules (STRICT):
- English only.
- First line: a short greeting. Right after it, open with ONE short question.
- Before the end: ONE short question, then a closing greeting, then the name on the last line.
- Put each sentence on its own line. Do NOT add empty lines anywhere.
- Use "," never ";".
- Humanized and concise. Total length MUST be under 1200 characters.
"""


def generate_proposal_openai(api_key: str, model: str, data: ProposalInput) -> str:
    client = OpenAI(api_key=api_key)

    portfolio_block = "\n".join(f"- {u}" for u in data.portfolio_urls)
    profile_block = "\n- ".join(data.your_profile_bullets)
    suggested_questions = "\n- ".join(data.questions) if data.questions else "(none)"

    user_prompt = f"""Job title: {data.title}

Job description:
{data.description}

Required skills (weave the relevant ones into the proposal naturally): {data.skills or "(none listed)"}

Budget: {data.budget_min}-{data.budget_max} {data.currency}

My proof bullets (use these, do NOT invent new ones):
- {profile_block}

My portfolio URLs (reference only these, pick the most relevant):
{portfolio_block}

Suggested questions you may ask (choose/rewrite, keep them short):
- {suggested_questions}

Sign the letter as: {data.signature_name}

Reference style (for TONE and structure only — follow the STRICT formatting rules above, not this example's spacing):
{STYLE_EXAMPLE}

Write the proposal now."""

    # Responses API (recommended for new builds).
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": user_prompt},
        ],
    )
    # The SDK returns a structured response; simplest is `output_text`.
    return resp.output_text.strip()
