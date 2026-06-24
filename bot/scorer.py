from __future__ import annotations

import re
from dataclasses import dataclass

from .util import clamp_int

RED_FLAG_PATTERNS = [
    r"telegram",
    r"whatsapp",
    r"pay.*upfront.*gift",
    r"crypto giveaway",
    r"easy money",
]

@dataclass(frozen=True)
class ScoreResult:
    eligible: bool
    score: int
    reasons: list[str]

def score_project(
    title: str,
    description: str,
    skills_csv: str,
    currency: str | None,
    budget_min: float | None,
    budget_max: float | None,
    min_budget_usd: int,
    required_skills: list[str],
    min_skill_matches: int = 1,
) -> ScoreResult:
    reasons: list[str] = []
    score = 0

    text = f"{title}\n{description}".lower()
    skills_text = (skills_csv or "").lower()
    currency_code = (currency or "").strip().upper()

    if currency_code == "INR":
        reasons.append("currency_inr")
        return ScoreResult(False, 0, reasons)

    # Red flags
    # for pat in RED_FLAG_PATTERNS:
    #     if re.search(pat, text):
    #         reasons.append(f"red_flag:{pat}")
    #         return ScoreResult(False, 0, reasons)

    # Budget check
    max_budget = budget_max if budget_max is not None else budget_min
    if max_budget is None:
        reasons.append("no_budget_info")
        score += 5
    else:
        # if max_budget < min_budget_usd:
        #     reasons.append(f"budget_too_low:{max_budget}")
        #     return ScoreResult(False, 0, reasons)
        score += 25
        # if max_budget >= min_budget_usd * 2:
        #     score += 10

    # Skill match
    matches = 0
    for s in required_skills:
        s2 = s.lower()
        if s2 in text or s2 in skills_text:
            matches += 1

    if matches < max(1, int(min_skill_matches)):
        reasons.append(f"skill_matches_too_low:{matches}")
        return ScoreResult(False, 0, reasons)

    score += min(50, matches * 15)
    if matches >= 2:
        score += 10
        reasons.append("multi_skill_bonus")
    if matches >= 4:
        score += 10
        reasons.append("strong_skill_fit")
    reasons.append(f"skill_matches:{matches}")

    # Clarity heuristic (longer descriptions often mean clearer requirements)
    if len(description or "") > 600:
        score += 10
        reasons.append("good_detail")
    elif len(description or "") > 200:
        score += 5
        reasons.append("some_detail")

    score = clamp_int(score, 0, 100)
    eligible = score >= 1
    return ScoreResult(eligible, score, reasons)
