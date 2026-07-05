"""Client read APIs — list, detail (profile + goals), and holdings.

All non-AI. Holdings/portfolio are DERIVED from `transactions` × `nav_history`
(via the `latest_holdings` view for current value, and a lateral month-series
query for value-over-time). Nothing here writes.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from sim_kernel import pipelines

from ..config import settings
from ..db import get_session
from ..engine import market
from ..engine.loader import load_client_state
from ..gpu import client as gpu_client
from ..schemas import (
    CategoryAllocation,
    ClientDetail,
    ClientInsights,
    ClientRow,
    GoalInsight,
    GoalOut,
    HoldingOut,
    HoldingsResponse,
    SipOut,
    SipsResponse,
    TimePoint,
    TransactionOut,
    TransactionsResponse,
    TxnCommitRequest,
    TxnCommitResponse,
)

router = APIRouter(prefix="/clients", tags=["clients"])

# Concentration thresholds (IMPLEMENTATION.md §5, concentration_flags).
FUND_CONCENTRATION = 0.25
CATEGORY_CONCENTRATION = 0.40

# required_sip runs ~20 bisection sims; cap paths so the live call stays snappy.
REQUIRED_SIP_PATHS = 4000


@router.get("", response_model=list[ClientRow])
def list_clients(
    session: Session = Depends(get_session),
    risk_profile: str | None = Query(None, description="filter by risk profile"),
    q: str | None = Query(None, description="case-insensitive name search"),
    limit: int = Query(500, ge=1, le=1000),
) -> list[ClientRow]:
    """The book table: every client with derived portfolio value + goal count."""
    sql = """
        select c.id, c.name, c.age, c.risk_profile,
               coalesce(h.total_value, 0) as portfolio_value,
               coalesce(h.fund_count, 0)  as fund_count,
               coalesce(g.goal_count, 0)  as goal_count
        from clients c
        left join (
            select client_id, sum(value) as total_value, count(*) as fund_count
            from latest_holdings group by client_id
        ) h on h.client_id = c.id
        left join (
            select client_id, count(*) as goal_count
            from goals group by client_id
        ) g on g.client_id = c.id
        where (cast(:risk_profile as text) is null or c.risk_profile = :risk_profile)
          and (cast(:q as text) is null or c.name ilike '%' || :q || '%')
        order by portfolio_value desc
        limit :limit
    """
    rows = session.execute(
        text(sql), {"risk_profile": risk_profile, "q": q, "limit": limit}
    ).mappings()
    return [
        ClientRow(
            id=r["id"],
            name=r["name"],
            age=r["age"],
            risk_profile=r["risk_profile"],
            portfolio_value=float(r["portfolio_value"]),
            goal_count=r["goal_count"],
            fund_count=r["fund_count"],
        )
        for r in rows
    ]


@router.get("/{client_id}", response_model=ClientDetail)
def get_client(
    client_id: int, session: Session = Depends(get_session)
) -> ClientDetail:
    """Profile + goals, each goal annotated with the value currently funding it."""
    client = session.execute(
        text("select id, name, age, risk_profile from clients where id = :id"),
        {"id": client_id},
    ).mappings().first()
    if client is None:
        raise HTTPException(status_code=404, detail="client not found")

    total_value = session.execute(
        text("select coalesce(sum(value), 0) from latest_holdings where client_id = :id"),
        {"id": client_id},
    ).scalar_one()

    # Per-goal funded value: holdings tagged to the goal via goal_holdings.
    goal_rows = session.execute(
        text(
            """
            select g.id, g.name, g.target_amount, g.target_date,
                   coalesce(sum(h.value), 0) as funded_value
            from goals g
            left join goal_holdings gh
              on gh.goal_id = g.id and gh.client_id = g.client_id
            left join latest_holdings h
              on h.client_id = gh.client_id and h.fund_id = gh.fund_id
            where g.client_id = :id
            group by g.id, g.name, g.target_amount, g.target_date
            order by g.target_date nulls last
            """
        ),
        {"id": client_id},
    ).mappings()

    goals = [
        GoalOut(
            id=r["id"],
            name=r["name"],
            target_amount=float(r["target_amount"]) if r["target_amount"] is not None else None,
            target_date=r["target_date"],
            funded_value=float(r["funded_value"]),
        )
        for r in goal_rows
    ]

    return ClientDetail(
        id=client["id"],
        name=client["name"],
        age=client["age"],
        risk_profile=client["risk_profile"],
        portfolio_value=float(total_value),
        goals=goals,
    )


@router.get("/{client_id}/holdings", response_model=HoldingsResponse)
def get_holdings(
    client_id: int, session: Session = Depends(get_session)
) -> HoldingsResponse:
    """Per-fund holdings, category roll-up, concentration flags, value-over-time."""
    exists = session.execute(
        text("select 1 from clients where id = :id"), {"id": client_id}
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="client not found")

    # Per-fund current holdings (units × latest NAV) from the derived view.
    holding_rows = session.execute(
        text(
            """
            select h.fund_id, f.name as fund_name, f.amc, h.category,
                   h.units, h.value,
                   (select nh.nav from nav_history nh
                    where nh.fund_id = h.fund_id
                    order by nh.date desc limit 1) as nav
            from latest_holdings h
            join funds f on f.id = h.fund_id
            where h.client_id = :id
            order by h.value desc
            """
        ),
        {"id": client_id},
    ).mappings().all()

    total = sum(float(r["value"]) for r in holding_rows)

    holdings: list[HoldingOut] = []
    cat_totals: dict[str, float] = {}
    for r in holding_rows:
        value = float(r["value"])
        holdings.append(
            HoldingOut(
                fund_id=r["fund_id"],
                fund_name=r["fund_name"],
                amc=r["amc"],
                category=r["category"],
                units=float(r["units"]),
                nav=float(r["nav"]) if r["nav"] is not None else 0.0,
                value=value,
                weight=(value / total) if total else 0.0,
            )
        )
        cat_totals[r["category"]] = cat_totals.get(r["category"], 0.0) + value

    allocation = [
        CategoryAllocation(
            category=cat,
            value=val,
            weight=(val / total) if total else 0.0,
        )
        for cat, val in sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    flags: list[str] = []
    if total and holdings and max(h.value for h in holdings) / total > FUND_CONCENTRATION:
        flags.append("concentrated_fund")
    if total and cat_totals and max(cat_totals.values()) / total > CATEGORY_CONCENTRATION:
        flags.append("concentrated_category")

    value_over_time = _value_over_time(session, client_id)

    return HoldingsResponse(
        client_id=client_id,
        total_value=total,
        holdings=holdings,
        allocation=allocation,
        flags=flags,
        value_over_time=value_over_time,
    )


def _value_over_time(session: Session, client_id: int) -> list[TimePoint]:
    """Reconstruct month-end portfolio value AND net invested capital from the ledger.

    Per month-end (from first transaction to today):
    - value:    units held per fund (cumulative signed units up to that date) ×
                latest NAV on/before that date, summed across funds.
    - invested: cumulative net cash in at cost (buys − redeems, in ₹) up to that date.
    Both derived — nothing is materialized.
    """
    rows = session.execute(
        text(
            """
            with client_funds as (
                select distinct fund_id from transactions where client_id = :id
            ),
            bounds as (
                select date_trunc('month', min(date)) as start_m
                from transactions where client_id = :id
            ),
            months as (
                select (generate_series(
                    (select start_m from bounds),
                    date_trunc('month', current_date),
                    interval '1 month'
                ) + interval '1 month' - interval '1 day')::date as m_end
            ),
            val as (
                select m.m_end as date,
                       coalesce(sum(pos.units * nav.nav), 0) as value
                from months m
                cross join client_funds cf
                left join lateral (
                    select sum(case when t.type = 'buy' then t.units else -t.units end) as units
                    from transactions t
                    where t.client_id = :id and t.fund_id = cf.fund_id and t.date <= m.m_end
                ) pos on true
                left join lateral (
                    select nh.nav from nav_history nh
                    where nh.fund_id = cf.fund_id and nh.date <= m.m_end
                    order by nh.date desc limit 1
                ) nav on true
                group by m.m_end
            )
            select v.date, v.value,
                   coalesce((
                       select sum(case when t.type = 'buy' then t.amount else -t.amount end)
                       from transactions t
                       where t.client_id = :id and t.date <= v.date
                   ), 0) as invested
            from val v
            order by v.date
            """
        ),
        {"id": client_id},
    ).mappings()
    return [
        TimePoint(date=r["date"], value=float(r["value"]), invested=float(r["invested"]))
        for r in rows
    ]


@router.get("/{client_id}/sips", response_model=SipsResponse)
def get_sips(
    client_id: int, session: Session = Depends(get_session)
) -> SipsResponse:
    """The client's forward-looking SIP schedule (per-fund monthly contributions)."""
    exists = session.execute(
        text("select 1 from clients where id = :id"), {"id": client_id}
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="client not found")

    rows = session.execute(
        text(
            """
            select s.id, s.fund_id, f.name as fund_name, f.category,
                   s.monthly_amount, s.stepup_rate, s.start_date, s.active
            from sip_schedule s
            join funds f on f.id = s.fund_id
            where s.client_id = :id
            order by s.active desc, s.monthly_amount desc
            """
        ),
        {"id": client_id},
    ).mappings()

    sips = [
        SipOut(
            id=r["id"],
            fund_id=r["fund_id"],
            fund_name=r["fund_name"],
            category=r["category"],
            monthly_amount=float(r["monthly_amount"]),
            stepup_rate=float(r["stepup_rate"]) if r["stepup_rate"] is not None else 0.0,
            start_date=r["start_date"],
            active=r["active"],
        )
        for r in rows
    ]
    total_monthly = sum(s.monthly_amount for s in sips if s.active)
    return SipsResponse(client_id=client_id, total_monthly=total_monthly, sips=sips)


@router.get("/{client_id}/transactions", response_model=TransactionsResponse)
def get_transactions(
    client_id: int, session: Session = Depends(get_session)
) -> TransactionsResponse:
    """The client's buy/redeem ledger — the source of truth for holdings."""
    exists = session.execute(
        text("select 1 from clients where id = :id"), {"id": client_id}
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="client not found")

    rows = session.execute(
        text(
            """
            select t.id, t.fund_id, f.name as fund_name, f.category,
                   t.date, t.type, t.units, t.nav, t.amount
            from transactions t
            join funds f on f.id = t.fund_id
            where t.client_id = :id
            order by t.date desc, t.id desc
            """
        ),
        {"id": client_id},
    ).mappings()

    transactions = [
        TransactionOut(
            id=r["id"],
            fund_id=r["fund_id"],
            fund_name=r["fund_name"],
            category=r["category"],
            date=r["date"],
            type=r["type"],
            units=float(r["units"]),
            nav=float(r["nav"]),
            amount=float(r["amount"]),
        )
        for r in rows
    ]
    return TransactionsResponse(client_id=client_id, transactions=transactions)


@router.post("/{client_id}/transactions", response_model=TxnCommitResponse)
def commit_transactions(
    client_id: int, req: TxnCommitRequest, session: Session = Depends(get_session)
) -> TxnCommitResponse:
    """Commit advisor-confirmed rows to the ledger — the *write* half of the NL data-entry
    flow (the Copilot's add_transactions only parses). Holdings re-derive automatically
    from the `latest_holdings` view; nothing else is materialized."""
    exists = session.execute(
        text("select 1 from clients where id = :id"), {"id": client_id}
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="client not found")
    if not req.rows:
        raise HTTPException(status_code=400, detail="no rows to commit")

    ids: list[int] = []
    for r in req.rows:
        if r.type not in ("buy", "redeem"):
            raise HTTPException(status_code=400, detail=f"bad transaction type: {r.type}")
        fund_ok = session.execute(
            text("select 1 from funds where id = :fid"), {"fid": r.fund_id}
        ).first()
        if fund_ok is None:
            raise HTTPException(status_code=400, detail=f"unknown fund_id: {r.fund_id}")
        new_id = session.execute(
            text(
                """
                insert into transactions (client_id, fund_id, date, type, units, nav, amount)
                values (:client_id, :fund_id, :date, :type, :units, :nav, :amount)
                returning id
                """
            ),
            {
                "client_id": client_id, "fund_id": r.fund_id, "date": r.date,
                "type": r.type, "units": r.units, "nav": r.nav, "amount": r.amount,
            },
        ).scalar_one()
        ids.append(int(new_id))
    session.commit()
    return TxnCommitResponse(client_id=client_id, inserted=len(ids), transaction_ids=ids)


# ── Deterministic insights (live single-client Monte Carlo) ────────────────────
@router.get("/{client_id}/insights", response_model=ClientInsights)
def get_insights(
    client_id: int, session: Session = Depends(get_session)
) -> ClientInsights:
    """Live Monte Carlo insights for one client: per-goal success probability, terminal
    spread and required-SIP-to-get-on-track, plus 1-year portfolio downside (VaR/CVaR,
    worst-case drawdown, suitability mismatch). The single-client hot path — one GPU job
    (local numpy or a RunPod worker, see `app/gpu/client.py`) covers every goal plus the
    required-SIP bisection in one round trip. The reported `elapsed_ms` (and `backend`)
    is the GPU-vs-CPU pitch number."""
    model = market.load_persisted(session) or market.resolve_market_model(session)
    state = load_client_state(session, client_id, model)
    if state is None:
        raise HTTPException(status_code=404, detail="client not found")

    confidence = settings.mc_confidence
    result = gpu_client.client_insights(
        state, model,
        n_paths=settings.mc_n_paths, required_sip_paths=REQUIRED_SIP_PATHS,
        seed=settings.mc_seed, confidence=confidence,
    )
    goals = [GoalInsight(**g) for g in result["goals"]]

    total = state.total
    mismatch = pipelines.suitability_mismatch(result["max_drawdown"], state.risk_profile)
    tolerable = pipelines.TOLERABLE_DD.get(state.risk_profile, pipelines.TOLERABLE_DD["balanced"])
    flags = pipelines.concentration_flags(state.fund_value, state.category_value, total)
    if any(not g.on_track for g in goals):
        flags.append("off_track")

    return ClientInsights(
        client_id=client_id,
        as_of_date=date.today(),
        backend=result["backend"],
        market_source=model.source,
        n_paths=result["n_paths"],
        seed=result["seed"],
        elapsed_ms=result["elapsed_ms"],
        confidence=confidence,
        var_95=result["var_95"],
        cvar_95=result["cvar_95"],
        max_drawdown=result["max_drawdown"],
        tolerable_dd=tolerable,
        suitability_mismatch=round(mismatch, 4),
        over_exposed=mismatch > 0,
        risk_score=round(min(100.0, result["max_drawdown"] * 100)),
        flags=flags,
        goals=goals,
    )
