"""ORM tables — mirrors `db/schema.sql` in IMPLEMENTATION.md §3.

Data-source tables: funds, nav_history.
Client-side tables: clients, goals, transactions, sip_schedule, goal_holdings.
Transactions are the source of truth; holdings are DERIVED via the latest_holdings
view (never materialized). The engine's later phases add assumptions/covariances/caches.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    amc: Mapped[Optional[str]] = mapped_column(String)
    scheme_code: Mapped[Optional[str]] = mapped_column(String, unique=True, index=True)
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
    age: Mapped[Optional[int]] = mapped_column(Integer)
    risk_profile: Mapped[Optional[str]] = mapped_column(String)

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
    name: Mapped[Optional[str]] = mapped_column(String)
    target_amount: Mapped[Optional[float]] = mapped_column(Numeric)  # ₹
    target_date: Mapped[Optional[date]] = mapped_column(Date)

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
    start_date: Mapped[Optional[date]] = mapped_column(Date)
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


# ── Market model (assumptions layer) ──────────────────────────────────────────
class Assumption(Base):
    """One row per CATEGORY. mu/sigma derived nightly from nav_history (GPU) or read
    from the hardcoded fallback (CPU). Shared across all clients. See engine/market.py."""

    __tablename__ = "assumptions"

    category: Mapped[str] = mapped_column(String, primary_key=True)
    mu: Mapped[Optional[float]] = mapped_column(Numeric)  # annual expected return
    sigma: Mapped[Optional[float]] = mapped_column(Numeric)  # annual volatility


class Covariance(Base):
    """The 14×14 category covariance Σ, stored as (cat_a, cat_b) pairs. Reassembled into
    a dense matrix and Cholesky-factored once at load time."""

    __tablename__ = "covariances"

    cat_a: Mapped[str] = mapped_column(String, primary_key=True)
    cat_b: Mapped[str] = mapped_column(String, primary_key=True)
    cov: Mapped[Optional[float]] = mapped_column(Numeric)


# ── Derived / output tables (filled by the book-analysis run) ─────────────────
class BaselineRun(Base):
    """Overnight sim cache — one dated row per client per run, append-only so history
    accumulates ("since last time"). run_whatif later reads the latest row."""

    __tablename__ = "baseline_runs"
    __table_args__ = (
        UniqueConstraint("client_id", "as_of_date", name="uq_baseline_client_date"),
        Index("ix_baseline_client_date_desc", "client_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    seed: Mapped[Optional[int]] = mapped_column(Integer)
    n_paths: Mapped[Optional[int]] = mapped_column(Integer)
    goals: Mapped[Optional[list]] = mapped_column(JSONB)  # [{goal_id, success_prob, terminal_pcts, shortfall}]
    var_95: Mapped[Optional[float]] = mapped_column(Numeric)
    cvar_95: Mapped[Optional[float]] = mapped_column(Numeric)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Numeric)
    suitability_mismatch: Mapped[Optional[float]] = mapped_column(Numeric)
    risk_score: Mapped[Optional[float]] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RadarOutput(Base):
    """Book-level ranked suitability list — one current row per client, refreshed each
    run. Powers the Risk Radar (rank_book)."""

    __tablename__ = "radar_output"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    suitability_mismatch: Mapped[Optional[float]] = mapped_column(Numeric)  # >0 => over-exposed
    tolerable_dd: Mapped[Optional[float]] = mapped_column(Numeric)
    simulated_dd: Mapped[Optional[float]] = mapped_column(Numeric)
    flags: Mapped[Optional[list]] = mapped_column(JSONB)  # ['off_track','concentrated_fund', ...]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ── Copilot chat persistence ──────────────────────────────────────────────────
class CopilotConversation(Base):
    """One Copilot conversation (a chat session). Messages hang off it in order; the
    title is derived from the first user turn. `client_id` is set when the chat was
    scoped to a client, else null (book-wide)."""

    __tablename__ = "copilot_conversations"
    __table_args__ = (
        Index("ix_copilot_conversations_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False, default="New conversation")
    client_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["CopilotMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="CopilotMessage.id",
    )


class CopilotMessage(Base):
    """One turn in a conversation. Assistant turns carry the full visible tool-call
    `trace` (the same [{tool, args, result}] the frontend renders) plus the GPU/CPU
    timing, so a reloaded conversation re-renders exactly as it first appeared. User
    turns keep `sent` — the id-rewritten text (@Name → "Name (client id N)") fed to
    the model — so replayed history stays id-accurate."""

    __tablename__ = "copilot_messages"
    __table_args__ = (
        CheckConstraint("role in ('user','assistant')", name="copilot_messages_role_check"),
        Index("ix_copilot_messages_conversation", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("copilot_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(String)  # display text (raw for user)
    sent: Mapped[Optional[str]] = mapped_column(String)  # id-rewritten text (user turns)
    trace: Mapped[Optional[list]] = mapped_column(JSONB)  # [{tool, args, result}] (assistant)
    backend: Mapped[Optional[str]] = mapped_column(String)  # 'numpy (CPU)' | 'cupy (GPU)'
    elapsed_ms: Mapped[Optional[float]] = mapped_column(Numeric)
    error: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped["CopilotConversation"] = relationship(back_populates="messages")


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

# Fast "latest row per client" lookup for the what-if hot path.
LATEST_BASELINE_VIEW = """
create view latest_baseline as
  select distinct on (client_id) *
  from baseline_runs
  order by client_id, as_of_date desc
"""


def create_views(engine) -> None:
    """(Re)create SQL views. Call after create_all — views depend on the tables."""
    with engine.begin() as conn:
        conn.exec_driver_sql("drop view if exists latest_holdings")
        conn.exec_driver_sql(LATEST_HOLDINGS_VIEW)
        conn.exec_driver_sql("drop view if exists latest_baseline")
        conn.exec_driver_sql(LATEST_BASELINE_VIEW)
