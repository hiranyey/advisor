"""Historical portfolio value, derived from the ledger — nothing materialized.

Shared by the holdings API (the `ValueChart` on a client's page) and the Copilot's
`project_portfolio` tool (the solid "value to date" half of the projection chart).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..schemas import TimePoint


def value_over_time(session: Session, client_id: int) -> list[TimePoint]:
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
