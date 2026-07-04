"""Pydantic request/response models for the read APIs.

These mirror the derived shapes the frontend renders: client list rows, the
client detail (profile + goals), holdings (per-fund + category roll-up +
concentration + value-over-time), and the book-wide analytics summary. Nothing
here touches the MC engine or LLM — pure reads over the seeded tables and the
`latest_holdings` view.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


# ── Clients ───────────────────────────────────────────────────────────────────
class ClientRow(BaseModel):
    """One row in the client list / book table."""

    id: int
    name: str
    age: int | None
    risk_profile: str | None
    portfolio_value: float
    goal_count: int
    fund_count: int


class GoalOut(BaseModel):
    id: int
    name: str | None
    target_amount: float | None
    target_date: date | None
    funded_value: float  # current value of holdings tagged to this goal


class ClientDetail(BaseModel):
    id: int
    name: str
    age: int | None
    risk_profile: str | None
    portfolio_value: float
    goals: list[GoalOut]


# ── Holdings ──────────────────────────────────────────────────────────────────
class HoldingOut(BaseModel):
    fund_id: int
    fund_name: str
    amc: str | None
    category: str
    units: float
    nav: float  # latest NAV
    value: float
    weight: float  # fraction of portfolio (0..1)


class CategoryAllocation(BaseModel):
    category: str
    value: float
    weight: float


class SipOut(BaseModel):
    id: int
    fund_id: int
    fund_name: str
    category: str
    monthly_amount: float
    stepup_rate: float
    start_date: date | None
    active: bool


class SipsResponse(BaseModel):
    client_id: int
    total_monthly: float  # sum of active SIP monthly amounts
    sips: list[SipOut]


class TimePoint(BaseModel):
    date: date
    value: float  # market value of holdings at month-end
    invested: float  # net capital put in (buys − redeems) at cost, cumulative


class HoldingsResponse(BaseModel):
    client_id: int
    total_value: float
    holdings: list[HoldingOut]
    allocation: list[CategoryAllocation]
    flags: list[str]  # e.g. ['concentrated_fund', 'concentrated_category']
    value_over_time: list[TimePoint]


# ── Book analytics ────────────────────────────────────────────────────────────
class RiskProfileCount(BaseModel):
    conservative: int = 0
    balanced: int = 0
    aggressive: int = 0


class BookSummary(BaseModel):
    total_clients: int
    total_aum: float
    total_goals: int
    avg_age: float | None
    by_risk_profile: RiskProfileCount
    allocation: list[CategoryAllocation]  # book-wide category roll-up
