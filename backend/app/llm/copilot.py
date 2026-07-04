"""The Copilot — a fixed six-tool LLM loop, not open chat.

The model decides which of the six tools to call with what arguments and narrates the
result; it never does the math itself. The tools hit the same engine + caches the REST
endpoints do (tools/impl.py). Every tool call + its result is captured as a visible trace
so the frontend can render the reasoning, not just the final sentence.

Tool set (IMPLEMENTATION.md §7): query_book · get_client_brief · run_whatif ·
stress_book · rank_book · add_transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from pydantic_ai import Agent, RunContext
from sqlalchemy.orm import Session

from ..engine.backend import BACKEND
from ..engine.categories import CATEGORIES
from ..engine.market import MarketModel
from ..engine.montecarlo import timer
from ..tools import impl

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
  category exposure).
- get_client_brief: the latest analysis for one client (goal probabilities, risk, suitability).
- run_whatif: re-simulate one client with a change (SIP delta, reallocation, lump sum,
  horizon shift, return shock) and return before/after.
- stress_book: apply one market shock across the whole book; return who breaches tolerance.
- rank_book: the suitability-mismatch "who do I call first" list.
- add_transactions: parse plain-English fund activity into rows for the advisor to confirm
  (this parses only; it does NOT commit).

When the advisor names a client but gives no id (the id is not written as "client id N"),
first call query_book with `name` to resolve them to a client_id, then use that id with the
other tools. Chain tools when useful (e.g. find a client, then pull their brief).

FORMATTING — your answer renders in a rich UI (GitHub-flavored markdown + a few special
blocks). Keep prose tight (2–4 sentences); the tool results are already shown as cards, so do
NOT restate raw JSON or repeat whole tables — surface only the numbers that matter. Use
markdown for structure: **bold** key terms, short bullet lists, and `###` headings only for
longer multi-part answers. Give ₹ amounts in Indian style (₹50,000 / ₹1.2 Cr).

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
        name: str | None = None,
    ) -> dict:
        """Find clients matching criteria. off_track = has a goal below tolerance;
        over_exposed = simulated downside exceeds risk tolerance; risk_profile is one of
        conservative/balanced/aggressive; category is one of the 14 category tags; name is
        a case-insensitive name search — use it to resolve a client referred to by name
        into their client_id before calling other tools."""
        return impl.query_book(
            ctx.deps.session, off_track, risk_profile, over_exposed, category, name
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
    def stress_book(ctx: RunContext[CopilotDeps], shock: dict, filters: dict | None = None) -> dict:
        """Apply one market shock across the whole book and return who breaches tolerance.
        shock: per-category deltas + optional horizon_months, e.g.
        {"high_risk_equity": -0.20, "horizon_months": 3}. Add "monte_carlo": true for
        correlated spillover. filters: e.g. {"risk_profile": "conservative"}."""
        return impl.stress_book(ctx.deps.session, ctx.deps.model, shock, filters)

    @agent.tool
    def rank_book(ctx: RunContext[CopilotDeps], limit: int = 25) -> dict:
        """The book-wide suitability-mismatch call list — who to call first, and why."""
        return impl.rank_book(ctx.deps.session, limit)

    @agent.tool
    async def add_transactions(ctx: RunContext[CopilotDeps], client_id: int, text: str) -> dict:
        """Parse plain-English fund activity into structured transaction rows for the
        advisor to confirm before commit. This PARSES ONLY and never writes."""
        from .parser import parse_transactions_text

        parsed = await parse_transactions_text(text)
        return impl.build_transaction_proposal(ctx.deps.session, client_id, text, parsed)

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


def _extract_trace(result) -> list[dict]:
    """Pair each tool call with its result into a visible, ordered trace."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart

    order: list[str] = []
    calls: dict[str, dict] = {}
    returns: dict[str, object] = {}
    for msg in result.new_messages():
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart):
                try:
                    args = part.args_as_dict()
                except Exception:
                    args = part.args if isinstance(part.args, dict) else {}
                calls[part.tool_call_id] = {"tool": part.tool_name, "args": args}
                order.append(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                returns[part.tool_call_id] = part.content

    return [
        {"tool": calls[cid]["tool"], "args": calls[cid]["args"], "result": returns.get(cid)}
        for cid in order if cid in calls
    ]


def run_copilot(
    session: Session, model: MarketModel, message: str,
    history: list[dict] | None = None, client_id: int | None = None,
) -> dict:
    """Run one Copilot turn: the tool loop plus a captured trace and timing.

    Returns {answer, trace:[{tool, args, result}], elapsed_ms, backend}. Raises
    LLMNotConfigured (from the provider) if no API key is set — the endpoint maps that
    to a clear 503."""
    agent = _agent()
    deps = CopilotDeps(session=session, model=model, client_id=client_id)
    with timer() as elapsed:
        result = agent.run_sync(message, message_history=_to_messages(history), deps=deps)
    return {
        "answer": result.output,
        "trace": _extract_trace(result),
        "elapsed_ms": round(elapsed() * 1000, 1),
        "backend": BACKEND,
    }
