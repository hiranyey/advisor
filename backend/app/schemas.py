"""Pydantic request/response models for the read APIs.

These mirror the derived shapes the frontend renders: client list rows, the
client detail (profile + goals), holdings (per-fund + category roll-up +
concentration + value-over-time), and the book-wide analytics summary. Nothing
here touches the MC engine or LLM — pure reads over the seeded tables and the
`latest_holdings` view.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

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


class TransactionOut(BaseModel):
    id: int
    fund_id: int
    fund_name: str
    category: str
    date: date | None
    type: str  # 'buy' | 'redeem'
    units: float
    nav: float
    amount: float


class TransactionsResponse(BaseModel):
    client_id: int
    transactions: list[TransactionOut]


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


# ── Client insights (live single-client Monte Carlo) ──────────────────────────
class GoalInsight(BaseModel):
    goal_id: int
    name: str | None
    target_amount: float
    horizon_months: int
    funded_value: float  # start value of the goal's tagged holdings
    success_prob: float  # share of futures that reach the target
    p5: float  # worst-case terminal value
    p50: float  # median terminal value
    p90: float  # optimistic terminal value
    shortfall_expected: float  # avg ₹ gap to target
    shortfall_worst: float  # P5 ₹ gap to target
    current_sip: float  # this goal's current total monthly SIP
    required_sip: float | None  # total monthly SIP to reach the confidence target
    on_track: bool  # success_prob >= confidence target


class ClientInsights(BaseModel):
    client_id: int
    as_of_date: date
    backend: str  # 'numpy (CPU)' | 'cupy (GPU)'
    market_source: str  # 'derived' | 'fallback' | 'persisted'
    n_paths: int
    seed: int
    elapsed_ms: float  # simulation wall-time — the GPU-vs-CPU pitch number
    confidence: float  # the required-SIP / on-track target (e.g. 0.80)
    # Portfolio risk (1-year, current holdings, no new SIP) — positive loss magnitudes
    var_95: float
    cvar_95: float
    max_drawdown: float  # worst-case (P-tail) loss magnitude
    tolerable_dd: float  # what the risk profile tolerates
    suitability_mismatch: float  # simulated − tolerable (>0 = over-exposed)
    over_exposed: bool
    risk_score: int  # 0..100
    flags: list[str]  # concentration + off_track
    goals: list[GoalInsight]


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


# ── Risk Radar (Monte Carlo suitability) ──────────────────────────────────────
class RadarKpis(BaseModel):
    mismatches: int  # clients whose simulated downside exceeds tolerance (mismatch > 0)
    watch: int  # within tolerance but close (0 >= mismatch > -0.05)
    median_goal_success: float | None  # median success probability across all goals
    book_var_95: float | None  # median client 1-yr VaR (loss fraction)
    book_cvar_95: float | None  # median client 1-yr CVaR
    off_track_clients: int  # >=1 goal below 50% success
    concentrated_clients: int  # single-fund or single-category over-exposure


class HeatmapCell(BaseModel):
    count: int
    state: str  # 'ok' | 'tight' | 'breach'


class HeatmapRow(BaseModel):
    profile: str
    tolerable_dd: float  # magnitude, e.g. 0.10
    cells: list[HeatmapCell]  # one per drawdown-bucket column


class HistBucket(BaseModel):
    label: str
    count: int


class ScatterPoint(BaseModel):
    """One dot in the book-wide risk-vs-goal-success quadrant — every scored client, not
    just the call-list top N."""

    client_id: int
    name: str
    risk_profile: str
    mismatch: float  # simulated − tolerable (>0 = over-exposed) — the x axis
    worst_goal_prob: float | None  # this client's worst goal success prob — the y axis
    portfolio_value: float  # drives dot radius


class RadarCallRow(BaseModel):
    client_id: int
    name: str
    risk_profile: str
    tolerable_dd: float  # magnitude
    simulated_dd: float  # magnitude (simulated 1-yr worst case)
    mismatch: float  # simulated − tolerable (>0 = over-exposed)
    portfolio_value: float  # ₹ — lets the UI show real money at risk, not just a % gap
    flags: list[str]
    top_category: str | None  # dominant exposure (the driver)
    top_weight: float
    worst_goal_name: str | None
    worst_goal_prob: float | None
    off_track_goals: int
    status: str  # 'breach' | 'watch' | 'ok'
    reason: str | None  # short LLM-written "why call them"; null until generated


class RadarResponse(BaseModel):
    as_of_date: date | None
    n_paths: int | None
    total_paths: int  # clients × paths — the headline "futures simulated"
    backend: str  # 'numpy (CPU)' | 'cupy (GPU)'
    market_source: str  # 'derived' | 'fallback'
    clients_scored: int
    heatmap_columns: list[str]
    kpis: RadarKpis
    heatmap: list[HeatmapRow]
    goal_success_hist: list[HistBucket]
    call_list: list[RadarCallRow]
    scatter: list[ScatterPoint]


# ── AI book insights (cached LLM narrative) ───────────────────────────────────
class BookInsightItem(BaseModel):
    kind: str  # 'risk' | 'concentration' | 'goals' | 'opportunity'
    severity: str  # 'good' | 'info' | 'watch' | 'critical'
    title: str
    body: str
    client_ids: list[int] = []


class BookInsightsResponse(BaseModel):
    as_of_date: date | None  # null if no insights have been generated yet
    headline: str | None  # one punchy line, may contain {{tag:span}} emphasis markup
    briefing: str | None  # the fuller paragraph, may also contain emphasis markup
    insights: list[BookInsightItem] = []
    llm_configured: bool


# ── Book trend (risk migration over time) ─────────────────────────────────────
class TrendPoint(BaseModel):
    as_of_date: date
    breach: int
    watch: int
    ok: int
    aum: float


class MoverRow(BaseModel):
    client_id: int
    name: str
    direction: str  # 'worsened' | 'improved'
    from_status: str
    to_status: str


class BookTrendResponse(BaseModel):
    points: list[TrendPoint]
    movers: list[MoverRow]


# ── Copilot (six-tool LLM loop) ───────────────────────────────────────────────
class ChatTurn(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str


class CopilotRequest(BaseModel):
    message: str  # the text sent to the model (mentions rewritten to "Name (client id N)")
    display_message: str | None = None  # what the advisor typed (@Name); shown on reload
    history: list[ChatTurn] = []  # ignored when conversation_id is set (DB is authoritative)
    client_id: int | None = None  # optional: the client the advisor is viewing
    conversation_id: int | None = None  # append to this chat; omit to start a new one


class ToolTrace(BaseModel):
    """One visible tool call: what the model invoked, with what args, and what came back.
    `result` is the raw tool payload the frontend renders as a card."""

    tool: str
    args: dict[str, Any] = {}
    result: Any = None


class CopilotResponse(BaseModel):
    answer: str  # the model's narrated, advisor-ready reply
    trace: list[ToolTrace]  # ordered tool-call trace (rendered inline)
    elapsed_ms: float  # simulation-only time this turn (excludes LLM latency)
    backend: str  # 'numpy (CPU)' | 'cupy (GPU)'
    conversation_id: int  # the chat this turn was persisted to


# ── Conversation history (DB-backed Copilot chats) ────────────────────────────
class ConversationRow(BaseModel):
    """One row in the history sidebar."""

    id: int
    title: str
    client_id: int | None
    message_count: int
    updated_at: datetime


class ConversationMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str | None
    trace: list[ToolTrace] = []
    backend: str | None = None
    elapsed_ms: float | None = None
    error: bool = False


class ConversationDetail(BaseModel):
    id: int
    title: str
    client_id: int | None
    messages: list[ConversationMessage]


# ── Transaction commit (confirm half of the NL data-entry flow) ───────────────
class TxnCommitRow(BaseModel):
    fund_id: int
    type: str  # 'buy' | 'redeem'
    date: date
    units: float
    nav: float
    amount: float


class TxnCommitRequest(BaseModel):
    rows: list[TxnCommitRow]


class TxnCommitResponse(BaseModel):
    client_id: int
    inserted: int
    transaction_ids: list[int]
