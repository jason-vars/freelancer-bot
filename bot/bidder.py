from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from freelancersdk.resources.projects import projects as fln_projects

@dataclass(frozen=True)
class BidRequest:
    project_id: int
    bidder_id: int
    description: str
    amount: float
    period_days: int
    milestone_percent: int

def place_bid(session, req: BidRequest) -> dict:
    # Official SDK function: place_project_bid(session, project_id, bidder_id, description, amount, period, milestone_percentage)
    return fln_projects.place_project_bid(
        session,
        req.project_id,
        req.bidder_id,
        req.description,
        req.amount,
        req.period_days,
        req.milestone_percent,
    )
