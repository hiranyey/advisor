"""The Copilot — a fixed six-tool LLM loop, not open chat.

The model decides which of the six tools to call with what arguments and narrates the
result; it never does the math itself. The tools hit the same engine + caches the REST
endpoints do (tools/impl.py). Every tool call + its result is captured as a visible trace
so the frontend can render the reasoning, not just the final sentence.

Tool set (IMPLEMENTATION.md §7): query_book · get_client_brief · run_whatif ·
stress_book · rank_book · add_transactions. Plus two dashboard-grounding tools:
get_book_insights · book_trend.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from sqlalchemy.orm import Session

from sim_kernel.categories import CATEGORIES
from sim_kernel.state import MarketModel

from ..gpu.client import backend_label
from ..tools import impl

# Schema reference for `run_sql` — every table/view the DB actually has (mirrors
# `backend/app/models.py`), kept here rather than derived at runtime so the model always
# sees the same stable names/columns regardless of what any one request touches.
_SCHEMA = """
TABLES
- funds(id, name, amc, scheme_code, category) — the only instrument type; category is one
  of the 14 asset-class tags below.
- nav_history(fund_id, date, nav) — daily NAV per fund; source for valuation and returns.
- clients(id, name, age, risk_profile) — risk_profile is conservative|balanced|aggressive.
- goals(id, client_id, name, target_amount, target_date) — a client's financial goals.
- transactions(id, client_id, fund_id, date, type[buy|redeem], units, nav, amount,
  created_at) — the buy/redeem ledger; SOURCE OF TRUTH for holdings (never edit).
- sip_schedule(id, client_id, fund_id, monthly_amount, stepup_rate, start_date, active) —
  forward-looking recurring contributions; active=false means a lapsed/stopped SIP.
- goal_holdings(client_id, fund_id, goal_id) — tags a fund holding to the goal it funds.
- assumptions(category, mu, sigma) — one row per category: annual expected return/vol.
- covariances(cat_a, cat_b, cov) — pairwise entries of the 14×14 category covariance Σ.
- baseline_runs(id, client_id, as_of_date, seed, n_paths, goals[jsonb], var_95, cvar_95,
  max_drawdown, suitability_mismatch, risk_score, created_at) — nightly Monte Carlo cache,
  one row per client per scored day. `goals` is a JSON array of {goal_id, name,
  target_amount, horizon_months, success_prob, terminal_pcts:{p5,p50,p90},
  shortfall:{expected,worst_p5}}.
- radar_output(client_id, suitability_mismatch, tolerable_dd, simulated_dd, flags[jsonb],
  reason, updated_at) — one CURRENT row per client (suitability_mismatch > 0 = over-exposed).
- radar_snapshots(id, client_id, as_of_date, suitability_mismatch, tolerable_dd,
  simulated_dd, flags[jsonb], worst_goal_prob, portfolio_value, created_at) — append-only
  history of radar_output, one row per (client, day) — use for trend-over-time questions.
- book_insights(id, as_of_date, kind[briefing|risk|concentration|goals|opportunity],
  severity[good|info|watch|critical], title, body, client_ids[jsonb], metric[jsonb],
  created_at) — cached LLM-written book narrative.
- copilot_conversations / copilot_messages — this chat's own history; rarely relevant to
  book/client questions.

VIEWS (derived, never materialized — always current)
- latest_holdings(client_id, fund_id, category, units, value) — net units × latest NAV per
  (client, fund); only positive positions. This is "current holdings."
- latest_baseline — one row per client: `select distinct on (client_id) * from
  baseline_runs order by client_id, as_of_date desc` (same columns as baseline_runs).

Prefer the dedicated tools when one fits — they're cached/faster. Reach for `run_sql` for
one-off lookups/aggregates the other tools don't cover (e.g. "which clients have a lapsed
SIP", "average client age by risk profile", "how many funds does each AMC have").
""".strip()

_SYSTEM = f"""
You are the AdvisorOS Copilot for a financial advisor who manages a book of mutual-fund
clients. You reason over the book and each client, and you call tools to get real numbers —
you NEVER estimate probabilities, losses, or valuations yourself.

The only instrument type is mutual funds. Every fund maps to one of these 14 category tags
(the "asset classes"): {", ".join(CATEGORIES)}. When a tool needs a category, use one of
these exact tags (map user phrasing: "small cap"/"small-cap" → high_risk_equity,
"debt"/"bonds" → good_debt, "gold" → gold, etc.).

Your tools:
- query_book: find clients matching criteria (off-track, over-exposed, risk profile,
  category exposure, over-concentrated in a single fund house).
- get_client_brief: the latest analysis for one client (goal probabilities, risk, suitability).
- run_whatif: re-simulate one client with a change (SIP delta, reallocation, lump sum,
  horizon shift, return shock) and return before/after goal probabilities.
- project_portfolio: project one client's portfolio VALUE forward N years (default 10),
  combining their actual value-to-date with a future best/median/worst-case range — use
  this for "portfolio health/value over the next N years" and for SIP/lump-sum/reallocation
  what-ifs the advisor wants to see as a value-over-time chart (run_whatif is for goal
  success-probability before/after instead).
- stress_book: apply one market shock across the whole book; return who breaches tolerance.
- rank_book: the suitability-mismatch "who do I call first" list.
- rank_goal_shortfalls: every off-track goal book-wide, ranked by expected ₹ shortfall —
  "which goals have the biggest funding gap" (a different cut than rank_book's risk ranking).
- add_transactions: parse plain-English fund activity into rows for the advisor to confirm
  (this parses only; it does NOT commit).
- get_book_insights: the cached AI-written morning briefing + insight cards shown on the
  dashboard — use this to ground "what stood out" / "why did you flag X" questions instead
  of re-deriving an opinion yourself.
- book_trend: breach/watch/ok counts over recent scored days plus who changed status since
  the last run — use this for "is my book getting riskier" / "who newly needs a call."
- run_sql: run one ad-hoc read-only SELECT/WITH query for anything the tools above don't
  cover. The schema is:

{_SCHEMA}

When the advisor names a client but gives no id (the id is not written as "client id N"),
first call query_book with `name` to resolve them to a client_id, then use that id with the
other tools. Chain tools when useful (e.g. find a client, then pull their brief).

FORMATTING — your answer renders in a rich UI (GitHub-flavored markdown + a few special
blocks). Keep prose tight (2–4 sentences); the tool results are already shown as cards, so do
NOT restate raw JSON or repeat whole tables — surface only the numbers that matter. NEVER
paste a tool's raw array/list data into your answer, as prose, a bulleted transcription, or a
code block — every tool result already renders as its own card (and, for project_portfolio,
a chart) directly above your answer; repeating it adds nothing and looks broken. This matters
most for project_portfolio (use only its `headline` field) and run_whatif/rank_book/
rank_goal_shortfalls (whose full lists are already tables) — if you catch yourself about to
write more than one or two numbers from a list a tool returned, stop and summarize instead.
Use markdown for structure: **bold** key terms, short bullet lists, and `###` headings only
for longer multi-part answers. Give ₹ amounts in Indian style (₹50,000 / ₹1.2 Cr).

You may also emit these visualization blocks as fenced code blocks whose info-string is the
block type and whose body is strict JSON (double quotes, no trailing commas, no comments).
Use them to make the key point pop — prefer 0–3 per answer, not one per number. Pick `tone`
by whether the number is good or bad for the client (over-exposed / off-track / a loss = bad).

- One takeaway/warning box at the top when there is a clear headline (tone: good|bad|warn|info|tip):
  ```callout
  {{"tone":"bad","title":"Call first","text":"3 conservative clients breach tolerance."}}
  ```
- A row of 1–4 headline metric tiles (tone: good|bad|warn|neutral):
  ```stats
  [{{"label":"Breaches","value":"33","tone":"bad"}},{{"label":"Book AUM","value":"₹1.2 Cr","tone":"neutral"}}]
  ```
- A probability/share as a bar (value is a percentage 0–100):
  ```progress
  {{"label":"Retirement goal","value":31,"tone":"bad","caption":"target ₹2.58 Cr in 18 yrs"}}
  ```
- A before → after change (ideal for what-ifs):
  ```compare
  {{"label":"Retirement success","before":"31%","after":"44%","tone":"good"}}
  ```

Today is {date.today().isoformat()}.
""".strip()


@dataclass
class CopilotDeps:
    session: Session
    model: MarketModel
    client_id: int | None = None


@lru_cache(maxsize=1)
def _agent():
    from .provider import get_model

    agent = Agent(get_model(), deps_type=CopilotDeps, instructions=_SYSTEM)

    @agent.instructions
    def _client_context(ctx: RunContext[CopilotDeps]) -> str:
        if ctx.deps.client_id:
            return f"The advisor is currently viewing client id {ctx.deps.client_id}; " \
                   "assume 'this client' refers to them unless another is named."
        return ""

    @agent.tool
    def query_book(
        ctx: RunContext[CopilotDeps],
        off_track: bool | None = None,
        risk_profile: str | None = None,
        over_exposed: bool | None = None,
        category: str | None = None,
        over_concentrated_amc: bool | None = None,
        name: str | None = None,
    ) -> dict:
        """Find clients matching criteria. off_track = has a goal below tolerance;
        over_exposed = simulated downside exceeds risk tolerance; risk_profile is one of
        conservative/balanced/aggressive; category is one of the 14 category tags;
        over_concentrated_amc = more than 40% of the portfolio sits with a single fund
        house ("overexposed to one AMC" / "one fund house"); name is a case-insensitive
        name search — use it to resolve a client referred to by name into their client_id
        before calling other tools."""
        return impl.query_book(
            ctx.deps.session, off_track, risk_profile, over_exposed, category,
            over_concentrated_amc, name,
        )

    @agent.tool
    def get_client_brief(ctx: RunContext[CopilotDeps], client_id: int) -> dict:
        """Pull the latest per-client analysis: profile, goal probabilities, portfolio
        downside + suitability, top allocation, monthly SIP commitment, and flags."""
        return impl.get_client_brief(ctx.deps.session, client_id)

    @agent.tool
    def run_whatif(
        ctx: RunContext[CopilotDeps],
        client_id: int,
        sip_delta: float | None = None,
        lump_sum: float | None = None,
        reallocate: dict | None = None,
        reduce_concentration: dict | None = None,
        horizon_shift: int | None = None,
        return_shock: dict | None = None,
    ) -> dict:
        """Re-simulate one client with a change, returning before/after goal probabilities
        and portfolio downside. sip_delta: ₹ change to monthly SIP. lump_sum: one-time ₹
        (+ invest / − withdraw). reallocate: {"from": cat, "to": cat, "pct": 0.10}.
        reduce_concentration: {"category": cat, "cap": 0.25, "to": cat}. horizon_shift:
        ± months on every goal. return_shock: {category: annual_return_delta}."""
        return impl.run_whatif(
            ctx.deps.session, ctx.deps.model, client_id,
            sip_delta=sip_delta, lump_sum=lump_sum, reallocate=reallocate,
            reduce_concentration=reduce_concentration, horizon_shift=horizon_shift,
            return_shock=return_shock,
        )

    @agent.tool
    def project_portfolio(
        ctx: RunContext[CopilotDeps],
        client_id: int,
        horizon_years: int = 10,
        sip_delta: float | None = None,
        lump_sum: float | None = None,
        reallocate: dict | None = None,
        reduce_concentration: dict | None = None,
        return_shock: dict | None = None,
    ) -> dict:
        """Project one client's portfolio value forward `horizon_years` (default 10):
        their actual value-to-date plus a future P5/P50/P90 range, rendered as a chart.
        Use for "how will this portfolio look/what's its health over the next N years" and
        for SIP/lump-sum/reallocation what-ifs the advisor wants to see as a value-over-time
        chart. sip_delta: ₹ change to monthly SIP (e.g. +5000 for "add ₹5,000/mo"). lump_sum:
        one-time ₹ (+ invest / − withdraw). reallocate/reduce_concentration/return_shock:
        same shape as run_whatif.
        The result's `history` and `projection` arrays are ALREADY rendered as a chart —
        do not read them, quote them, or paste any part of them (as prose, a table, or a
        code block). Narrate ONLY from the `headline` field (current value, monthly SIP,
        and the median/worst/best value at the horizon) plus `levers`."""
        return impl.project_portfolio(
            ctx.deps.session, ctx.deps.model, client_id, horizon_years=horizon_years,
            sip_delta=sip_delta, lump_sum=lump_sum, reallocate=reallocate,
            reduce_concentration=reduce_concentration, return_shock=return_shock,
        )

    @agent.tool
    def stress_book(ctx: RunContext[CopilotDeps], shock: dict, filters: dict | None = None) -> dict:
        """Apply one market shock across the whole book and return who breaches tolerance.
        shock: per-category deltas + optional horizon_months, e.g.
        {"high_risk_equity": -0.20, "horizon_months": 3}. Runs Monte Carlo (correlated
        spillover) by default; set "monte_carlo": false for plain weight×shock arithmetic.
        filters: e.g. {"risk_profile": "conservative"}."""
        return impl.stress_book(ctx.deps.session, ctx.deps.model, shock, filters)

    @agent.tool
    def rank_book(ctx: RunContext[CopilotDeps], limit: int = 25) -> dict:
        """The book-wide suitability-mismatch call list — who to call first, and why."""
        return impl.rank_book(ctx.deps.session, limit)

    @agent.tool
    def rank_goal_shortfalls(ctx: RunContext[CopilotDeps], limit: int = 25) -> dict:
        """Every off-track goal across the whole book, ranked by expected ₹ shortfall —
        "which goals have the biggest funding gap, and by when" (a different cut than
        rank_book, which ranks by risk/suitability instead of goal funding gap)."""
        return impl.rank_goal_shortfalls(ctx.deps.session, limit)

    @agent.tool
    def get_book_insights(ctx: RunContext[CopilotDeps]) -> dict:
        """The cached AI-written morning briefing + insight cards shown on the dashboard
        for the latest scored day. Use this to ground questions about what's notable in
        the book right now instead of forming a fresh opinion."""
        return impl.get_book_insights(ctx.deps.session)

    @agent.tool
    def book_trend(ctx: RunContext[CopilotDeps], lookback_days: int = 30) -> dict:
        """Breach/watch/ok client counts per scored day over the recent history, plus who
        moved between statuses between the latest two runs (newly breached / recovered)."""
        return impl.book_trend(ctx.deps.session, lookback_days)

    @agent.tool
    async def add_transactions(ctx: RunContext[CopilotDeps], client_id: int, text: str) -> dict:
        """Parse plain-English fund activity into structured transaction rows for the
        advisor to confirm before commit. This PARSES ONLY and never writes."""
        from .parser import parse_transactions_text

        parsed = await parse_transactions_text(text)
        return impl.build_transaction_proposal(ctx.deps.session, client_id, text, parsed)

    @agent.tool
    def run_sql(ctx: RunContext[CopilotDeps], query: str) -> dict:
        """Run one ad-hoc, read-only SQL query (a single SELECT or WITH statement) against
        the schema described in your instructions, for questions the other tools don't
        cover. Rejected if it isn't a single SELECT/WITH statement or contains a
        write/DDL keyword. Results are capped at 200 rows."""
        return impl.run_sql(ctx.deps.session, query)

    return agent


def _to_messages(history: list[dict] | None):
    """Rebuild pydantic-ai message history from prior {role, content} turns."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    msgs = []
    for h in history or []:
        content = h.get("content") or ""
        if h.get("role") == "user":
            msgs.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif h.get("role") == "assistant":
            msgs.append(ModelResponse(parts=[TextPart(content=content)]))
    return msgs


EventCallback = Callable[[dict], Awaitable[None]]


async def run_copilot(
    session: Session, model: MarketModel, message: str,
    history: list[dict] | None = None, client_id: int | None = None,
    on_event: EventCallback | None = None,
) -> dict:
    """Run one Copilot turn: the tool loop plus a captured trace and timing.

    If given, `on_event` is called live, in order, with one of:
      {"type": "reasoning", "text": str} — narration the model produced before a
        batch of tool calls (text that turns out to be the final answer, i.e. isn't
        followed by any tool call, is never emitted here — see below).
      {"type": "tool_call", "tool_call_id", "tool", "args"} — a tool call has started.
      {"type": "tool_result", "tool_call_id", "tool", "result"} — that call resolved.

    Returns {answer, trace:[{tool, args, result}], elapsed_ms, backend}. Raises
    LLMNotConfigured (from the provider) if no API key is set — the endpoint maps that
    to a clear 503.

    Narration is paired to its own turn's tool calls (not a later turn's) purely by call
    order: `event_stream_handler` below is invoked once per model-request node (that
    turn's narration, buffered) immediately followed by once per call-tools node for the
    *same* turn (that turn's tool calls) — so buffered text is flushed as `reasoning` the
    moment the next call emits a tool call. If the run ends without that happening, the
    buffered text was the final answer (already returned as `answer`), so it's dropped
    unflushed rather than emitted twice.
    """
    agent = _agent()
    deps = CopilotDeps(session=session, model=model, client_id=client_id)
    start = time.perf_counter()

    trace: list[dict] = []
    calls: dict[str, dict] = {}
    pending_reasoning: list[str] = []

    async def emit(event: dict) -> None:
        if on_event is not None:
            await on_event(event)

    async def handler(ctx, events) -> None:
        nonlocal pending_reasoning
        async for event in events:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                pending_reasoning.append(event.part.content)
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                pending_reasoning.append(event.delta.content_delta)
            elif isinstance(event, FunctionToolCallEvent):
                text = "".join(pending_reasoning).strip()
                pending_reasoning = []
                if text:
                    await emit({"type": "reasoning", "text": text})
                part = event.part
                try:
                    args = part.args_as_dict()
                except Exception:
                    args = part.args if isinstance(part.args, dict) else {}
                entry = {"tool": part.tool_name, "args": args, "result": None}
                calls[part.tool_call_id] = entry
                trace.append(entry)
                await emit({
                    "type": "tool_call", "tool_call_id": part.tool_call_id,
                    "tool": entry["tool"], "args": args,
                })
            elif isinstance(event, FunctionToolResultEvent):
                entry = calls.get(event.tool_call_id)
                result = getattr(event.part, "content", None)
                if entry is not None:
                    entry["result"] = result
                await emit({
                    "type": "tool_result", "tool_call_id": event.tool_call_id,
                    "tool": entry["tool"] if entry else None, "result": result,
                })

    result = await agent.run(
        message, message_history=_to_messages(history), deps=deps,
        event_stream_handler=handler,
    )
    return {
        "answer": result.output,
        "trace": trace,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
        "backend": backend_label(),
    }
