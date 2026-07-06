"""Client debrief suggestions — a one-shot structured-output agent (mirrors
insights.py's pattern, not copilot.py's tool loop) that turns one client's brief
into 10 concrete, shareable questions.

The suggestions are plain natural-language questions phrased the way an advisor
would type them into the Copilot, because that's exactly what happens next: the
advisor picks one and it's replayed verbatim as the message to
app.llm.copilot.run_copilot (see app.api.debrief), which does the actual tool
calls and narration. This file never touches the engine or the DB beyond reading
one client's cached brief.
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..tools import impl
from .provider import get_model

_INSTRUCTIONS = """
You are helping a financial advisor pick what to show one client in a shareable,
one-page report. You are given a JSON brief for a single client: their profile,
goals, current SIPs, portfolio allocation, and (if scored) risk/goal-probability
numbers.

Write exactly 10 short questions the advisor could ask about THIS client, each
one phrased as if the advisor typed it directly into a chat assistant that can
run Monte Carlo projections and what-ifs. Ground every question in the client's
actual numbers from the JSON — never invent a goal, fund, or SIP amount that
isn't in the input.

Cover a diverse mix (do not skip categories just because a category feels
repetitive — variety is the point):
- 2-3 SIP step-up scenarios using ROUND numbers relative to their actual current
  total monthly SIP (e.g. if their SIP is ₹25,000/month, ask about +₹10,000 and
  +₹20,000/month, not arbitrary numbers).
- 1 goal-on-track check naming a real goal from the input (only if they have goals).
- 1 market-downturn stress test (e.g. "what if equity markets fall 20% this year").
- 1 reallocation/rebalancing what-if, if their allocation data suggests a
  reasonable one (e.g. shifting some weight from their top category).
- 1 lump-sum what-if (a bonus or windfall invested today).
- 1 longer-horizon portfolio projection (e.g. "how will my portfolio look in
  15/20 years").
- 1 concentration or risk-mismatch check, if their flags/risk data suggest one is
  relevant; otherwise another goal or projection question.

Each question should be a single, self-contained sentence a client would find
meaningful (avoid jargon like "suitability mismatch" — say "risk level" instead).
Return exactly 10 questions, no numbering, no preamble.
""".strip()


class SuggestionsPayload(BaseModel):
    suggestions: list[str]


@lru_cache(maxsize=1)
def _agent():
    from pydantic_ai import Agent

    return Agent(get_model(), output_type=SuggestionsPayload, instructions=_INSTRUCTIONS)


async def generate_suggestions(session: Session, client_id: int) -> list[str]:
    """Generate 10 client-specific shareable questions. Raises LLMNotConfigured
    (from the provider) if no API key is set."""
    brief = impl.get_client_brief(session, client_id)
    prompt = f"Client brief:\n{json.dumps(brief, default=str)}"
    result = await _agent().run(prompt)
    return result.output.suggestions[:10]
