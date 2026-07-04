"""Rolling client DB state up into the 14-category vectors the engine consumes.

The engine never sees funds — only per-category vectors. This module is the bridge:
it reads the derived `latest_holdings` view, the SIP schedule, and the goal tags, then
produces `ClientState` objects (whole-book or single-client) with:

* `holdings` / `monthly_sip` — 14-vectors of ₹ per category,
* per-goal sub-portfolios (only the funds tagged to each goal, rolled up the same way),
* `fund_value` / `category_value` for concentration checks.

`mu` and `L` come from the market model and are attached here so a state is self-contained
enough to hand straight to `simulate()` / `stress_book()`.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from .categories import CAT_INDEX, N_CATEGORIES
from .market import MarketModel


def _horizon_months(target: date | None, as_of: date) -> int:
    if target is None:
        return 1
    months = (target.year - as_of.year) * 12 + (target.month - as_of.month)
    return max(months, 1)


@dataclass
class GoalState:
    goal_id: int
    name: str | None
    target_amount: float
    horizon_months: int
    holdings: np.ndarray                  # (14,) ₹ per category, this goal's funds only
    monthly_sip: np.ndarray              # (14,)
    stepup_rate: float = 0.0

    @property
    def start_value(self) -> float:
        return float(self.holdings.sum())


@dataclass
class ClientState:
    id: int
    name: str
    risk_profile: str
    holdings: np.ndarray                  # (14,) ₹ per category (whole portfolio)
    monthly_sip: np.ndarray              # (14,)
    stepup_rate: float
    fund_value: dict                     # {fund_id: ₹}  (for concentration)
    category_value: dict                 # {category_tag: ₹}  (nonzero only)
    goals: list[GoalState] = field(default_factory=list)
    mu: np.ndarray | None = None         # attached from the market model
    L: np.ndarray | None = None

    @property
    def total(self) -> float:
        return float(self.holdings.sum())

    def with_market(self, model: MarketModel) -> "ClientState":
        self.mu, self.L = model.mu, model.L
        return self


def _weighted_stepup(rows: list[tuple]) -> float:
    """Contribution-weighted average step-up across SIP rows [(amount, stepup), ...]."""
    total = sum(a for a, _ in rows)
    if total <= 0:
        return 0.0
    return sum(a * s for a, s in rows) / total


def load_client_states(
    session: Session, model: MarketModel, as_of: date | None = None,
    client_ids: list[int] | None = None,
) -> list[ClientState]:
    """Build ClientState for the whole book (or a subset), market model attached.

    Bulk-loads holdings, SIPs, goal tags and goals in four queries, then assembles in
    Python — one pass, no per-client round trips.
    """
    as_of = as_of or date.today()
    where = ""
    params: dict = {}
    if client_ids is not None:
        where = " where c.id = any(:ids)"
        params["ids"] = client_ids

    clients = session.execute(
        text(f"select c.id, c.name, c.risk_profile from clients c{where} order by c.id"),
        params,
    ).all()
    if not clients:
        return []
    ids = [cid for cid, _, _ in clients]

    # Per-fund holdings (one row per client+fund), the SIP schedule, goal tags, and goals.
    holdings = session.execute(text("""
        select client_id, fund_id, category, value
        from latest_holdings where client_id = any(:ids)
    """), {"ids": ids}).all()
    sips = session.execute(text("""
        select s.client_id, s.fund_id, f.category, s.monthly_amount, s.stepup_rate
        from sip_schedule s join funds f on f.id = s.fund_id
        where s.active and s.client_id = any(:ids)
    """), {"ids": ids}).all()
    goal_tags = session.execute(text("""
        select client_id, fund_id, goal_id from goal_holdings where client_id = any(:ids)
    """), {"ids": ids}).all()
    goals = session.execute(text("""
        select id, client_id, name, target_amount, target_date
        from goals where client_id = any(:ids)
    """), {"ids": ids}).all()

    # Index the raw rows by client for assembly.
    h_by_client: dict[int, list] = {i: [] for i in ids}
    for cid, fund_id, category, value in holdings:
        h_by_client[cid].append((fund_id, category, float(value)))
    s_by_client: dict[int, list] = {i: [] for i in ids}
    for cid, fund_id, category, amt, step in sips:
        s_by_client[cid].append((fund_id, category, float(amt), float(step or 0)))
    fund_goal: dict[int, dict] = {i: {} for i in ids}  # {client: {fund_id: goal_id}}
    for cid, fund_id, goal_id in goal_tags:
        fund_goal[cid][fund_id] = goal_id
    goals_by_client: dict[int, list] = {i: [] for i in ids}
    for gid, cid, name, target, tdate in goals:
        goals_by_client[cid].append((gid, name, target, tdate))

    states: list[ClientState] = []
    for cid, name, risk in clients:
        risk = risk or "balanced"
        holdings_vec = np.zeros(N_CATEGORIES)
        sip_vec = np.zeros(N_CATEGORIES)
        fund_value: dict = {}
        category_value: dict = {}

        for fund_id, category, value in h_by_client[cid]:
            idx = CAT_INDEX[category]
            holdings_vec[idx] += value
            fund_value[fund_id] = fund_value.get(fund_id, 0.0) + value
            category_value[category] = category_value.get(category, 0.0) + value

        client_sip_rows = []
        for _, category, amt, step in s_by_client[cid]:
            sip_vec[CAT_INDEX[category]] += amt
            client_sip_rows.append((amt, step))

        # Per-goal sub-portfolios: restrict holdings/SIPs to the funds tagged to the goal.
        tag = fund_goal[cid]
        goal_states: list[GoalState] = []
        for gid, gname, target, tdate in goals_by_client[cid]:
            g_holdings = np.zeros(N_CATEGORIES)
            g_sip = np.zeros(N_CATEGORIES)
            g_step_rows = []
            for fund_id, category, value in h_by_client[cid]:
                if tag.get(fund_id) == gid:
                    g_holdings[CAT_INDEX[category]] += value
            for fund_id, category, amt, step in s_by_client[cid]:
                if tag.get(fund_id) == gid:
                    g_sip[CAT_INDEX[category]] += amt
                    g_step_rows.append((amt, step))
            goal_states.append(GoalState(
                goal_id=gid, name=gname, target_amount=float(target or 0),
                horizon_months=_horizon_months(tdate, as_of),
                holdings=g_holdings, monthly_sip=g_sip,
                stepup_rate=_weighted_stepup(g_step_rows),
            ))

        states.append(ClientState(
            id=cid, name=name, risk_profile=risk,
            holdings=holdings_vec, monthly_sip=sip_vec,
            stepup_rate=_weighted_stepup(client_sip_rows),
            fund_value=fund_value, category_value=category_value,
            goals=goal_states, mu=model.mu, L=model.L,
        ))
    return states


def load_client_state(
    session: Session, client_id: int, model: MarketModel, as_of: date | None = None
) -> ClientState | None:
    """Single-client convenience (what-if hot path)."""
    states = load_client_states(session, model, as_of=as_of, client_ids=[client_id])
    return states[0] if states else None
