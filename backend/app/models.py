"""ORM tables — mirrors `db/schema.sql` in IMPLEMENTATION.md §3.

Data-source tables: funds, nav_history.
Client-side tables: clients, goals, transactions, sip_schedule, goal_holdings.
Transactions are the source of truth; holdings are DERIVED via the latest_holdings
view (never materialized). The engine's later phases add assumptions/covariances/caches.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .engine.categories import CATEGORIES

_CATEGORY_CHECK = "category in (" + ", ".join(f"'{c}'" for c in CATEGORIES) + ")"


class Fund(Base):
    """The only instrument type. `category` bridges a real fund to the engine's
    return dynamics; `scheme_code` is the AMFI code."""

    __tablename__ = "funds"
    __table_args__ = (CheckConstraint(_CATEGORY_CHECK, name="funds_category_check"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    amc: Mapped[str | None] = mapped_column(String)
    scheme_code: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False)

    nav_history: Mapped[list["NavHistory"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan"
    )


class NavHistory(Base):
    """Daily NAV series per fund. Powers valuation, portfolio-over-time, and the
    derived market model (mu/sigma/Sigma)."""

    __tablename__ = "nav_history"
    __table_args__ = (
        Index("ix_nav_history_fund_date_desc", "fund_id", "date"),
    )

    fund_id: Mapped[int] = mapped_column(
        ForeignKey("funds.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav: Mapped[float] = mapped_column(Numeric, nullable=False)

    fund: Mapped["Fund"] = relationship(back_populates="nav_history")


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint(
            "risk_profile in ('conservative','balanced','aggressive')",
            name="clients_risk_profile_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    age: Mapped[int | None] = mapped_column(Integer)
    risk_profile: Mapped[str | None] = mapped_column(String)

    goals: Mapped[list["Goal"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    sips: Mapped[list["SipSchedule"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    goal_holdings: Mapped[list["GoalHolding"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str | None] = mapped_column(String)
    target_amount: Mapped[float | None] = mapped_column(Numeric)  # ₹
    target_date: Mapped[date | None] = mapped_column(Date)

    client: Mapped["Client"] = relationship(back_populates="goals")
    goal_holdings: Mapped[list["GoalHolding"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )


class Transaction(Base):
    """Buy/redeem ledger — the SOURCE OF TRUTH for holdings. amount = units * nav."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type in ('buy','redeem')", name="transactions_type_check"),
        Index("ix_transactions_client_fund_date", "client_id", "fund_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    units: Mapped[float] = mapped_column(Numeric, nullable=False)
    nav: Mapped[float] = mapped_column(Numeric, nullable=False)  # NAV at execution
    amount: Mapped[float] = mapped_column(Numeric, nullable=False)  # units * nav
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    client: Mapped["Client"] = relationship(back_populates="transactions")


class SipSchedule(Base):
    """Forward-looking recurring contributions — the FUTURE counterpart to transactions."""

    __tablename__ = "sip_schedule"
    __table_args__ = (
        Index("ix_sip_schedule_client_fund", "client_id", "fund_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"))
    monthly_amount: Mapped[float] = mapped_column(Numeric, nullable=False)  # ₹/month
    stepup_rate: Mapped[float] = mapped_column(Numeric, default=0)  # e.g. 0.10 = +10%/yr
    start_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped["Client"] = relationship(back_populates="sips")


class GoalHolding(Base):
    """Tags a client's fund holding to the goal it funds. One goal per (client, fund)."""

    __tablename__ = "goal_holdings"
    __table_args__ = (
        Index("ix_goal_holdings_goal", "goal_id"),
    )

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    fund_id: Mapped[int] = mapped_column(
        ForeignKey("funds.id", ondelete="CASCADE"), primary_key=True
    )
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"))

    client: Mapped["Client"] = relationship(back_populates="goal_holdings")
    goal: Mapped["Goal"] = relationship(back_populates="goal_holdings")


# ── Derived holdings view (never materialized) ────────────────────────────────
# Net units × latest NAV per (client, fund), rolled up with the fund's category.
LATEST_HOLDINGS_VIEW = """
create view latest_holdings as
  select
    t.client_id,
    t.fund_id,
    f.category,
    sum(case when t.type='buy' then t.units else -t.units end) as units,
    sum(case when t.type='buy' then t.units else -t.units end)
      * (select nh.nav from nav_history nh
         where nh.fund_id = t.fund_id order by nh.date desc limit 1) as value
  from transactions t
  join funds f on f.id = t.fund_id
  group by t.client_id, t.fund_id, f.category
  having sum(case when t.type='buy' then t.units else -t.units end) > 0
"""


def create_views(engine) -> None:
    """(Re)create SQL views. Call after create_all — views depend on the tables."""
    with engine.begin() as conn:
        conn.exec_driver_sql("drop view if exists latest_holdings")
        conn.exec_driver_sql(LATEST_HOLDINGS_VIEW)
