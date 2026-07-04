"""Book-wide analytics — the non-AI half of the Risk Radar page.

Aggregate reads over the whole book: AUM, client/goal counts, risk-profile
breakdown, and the book-wide category allocation. The MC-driven suitability radar
(baseline_runs / radar_output) is a later phase; this ships the analytics that the
seeded tables already support.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import BookSummary, CategoryAllocation, RiskProfileCount

router = APIRouter(prefix="/book", tags=["book"])


@router.get("/summary", response_model=BookSummary)
def book_summary(session: Session = Depends(get_session)) -> BookSummary:
    total_clients = session.execute(text("select count(*) from clients")).scalar_one()
    total_goals = session.execute(text("select count(*) from goals")).scalar_one()
    avg_age = session.execute(text("select avg(age) from clients")).scalar_one()
    total_aum = session.execute(
        text("select coalesce(sum(value), 0) from latest_holdings")
    ).scalar_one()

    profile_rows = session.execute(
        text("select risk_profile, count(*) as n from clients group by risk_profile")
    ).mappings()
    counts = RiskProfileCount()
    for r in profile_rows:
        if r["risk_profile"] in ("conservative", "balanced", "aggressive"):
            setattr(counts, r["risk_profile"], r["n"])

    alloc_rows = session.execute(
        text(
            """
            select category, sum(value) as value
            from latest_holdings
            group by category
            order by value desc
            """
        )
    ).mappings().all()
    book_total = sum(float(r["value"]) for r in alloc_rows)
    allocation = [
        CategoryAllocation(
            category=r["category"],
            value=float(r["value"]),
            weight=(float(r["value"]) / book_total) if book_total else 0.0,
        )
        for r in alloc_rows
    ]

    return BookSummary(
        total_clients=total_clients,
        total_aum=float(total_aum),
        total_goals=total_goals,
        avg_age=float(avg_age) if avg_age is not None else None,
        by_risk_profile=counts,
        allocation=allocation,
    )
