"""Short, LLM-written "why call them" reasons for the Risk Radar's call list.

Unlike insights.py's book_insights (aggregate-only, to keep the model from inventing
client-level facts), this one IS grounded per client: each client's own already-computed
numbers are handed to the model and it only has to phrase them concisely — it can't
invent a client because it's told exactly which ids it's allowed to answer for.
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .provider import get_model

_INSTRUCTIONS = """
For each client in the given list, write ONE short phrase (under 12 words, no trailing
period) telling their advisor the main reason to call them today — plain language,
grounded ONLY in that client's own fields below. Never invent a number or fact, and
never answer for a client_id that isn't in the input.

Pick the single most important driver per client: usually the biggest category/fund
overweight relative to their risk profile, but lead with the worst off-track goal
instead if that's the more urgent story. Examples of the tone/length wanted:
"82% parked in high-risk equity, well past their comfort line"
"Retirement goal only 24% likely to hit on the current plan"
"Two funds make up 70% of the portfolio — concentrated bet"

Return exactly one reason per input client_id.
""".strip()


class CallReason(BaseModel):
    client_id: int
    reason: str


class CallReasonsPayload(BaseModel):
    reasons: list[CallReason]


@lru_cache(maxsize=1)
def _agent():
    from pydantic_ai import Agent

    return Agent(get_model(), output_type=CallReasonsPayload, instructions=_INSTRUCTIONS)


def _gather_candidates(session: Session, limit: int) -> list[dict]:
    """The same top-by-mismatch slice book_radar's call list shows, with the driver
    category and worst goal already computed elsewhere in the app."""
    rows = session.execute(
        text(
            """
            select c.id, c.risk_profile,
                   r.suitability_mismatch, r.simulated_dd, r.tolerable_dd, r.flags, b.goals
            from radar_output r
            join clients c on c.id = r.client_id
            left join latest_baseline b on b.client_id = r.client_id
            order by r.suitability_mismatch desc nulls last
            limit :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()

    top_cat_rows = session.execute(
        text(
            """
            with cat as (
                select client_id, category, sum(value) as v
                from latest_holdings group by client_id, category
            ),
            tot as (select client_id, sum(v) as t from cat group by client_id)
            select distinct on (cat.client_id)
                   cat.client_id, cat.category, cat.v / nullif(tot.t, 0) as weight
            from cat join tot on tot.client_id = cat.client_id
            order by cat.client_id, cat.v desc
            """
        )
    ).mappings().all()
    top_cat = {r["client_id"]: (r["category"], float(r["weight"] or 0)) for r in top_cat_rows}

    candidates = []
    for r in rows:
        goals = r["goals"] or []
        worst = min(goals, key=lambda g: g.get("success_prob", 1.0), default=None)
        cat, weight = top_cat.get(r["id"], (None, 0.0))
        candidates.append({
            "client_id": r["id"],
            "risk_profile": r["risk_profile"],
            "mismatch": round(float(r["suitability_mismatch"] or 0), 4),
            "simulated_dd": round(abs(float(r["simulated_dd"] or 0)), 4),
            "tolerable_dd": round(abs(float(r["tolerable_dd"] or 0)), 4),
            "flags": r["flags"] or [],
            "top_category": cat,
            "top_category_weight": round(weight, 4) if weight else None,
            "worst_goal_name": worst.get("name") if worst else None,
            "worst_goal_prob": worst.get("success_prob") if worst else None,
        })
    return candidates


async def generate_call_reasons(session: Session, limit: int = 25) -> None:
    """Generate + persist short call reasons for the top `limit` clients by mismatch —
    the same set the dashboard's call list shows. Raises on failure; the caller decides
    whether that's fatal (best-effort in the nightly task, surfaced by the refresh route)."""
    candidates = _gather_candidates(session, limit)
    if not candidates:
        return
    valid_ids = {c["client_id"] for c in candidates}

    result = await _agent().run(f"Clients:\n{json.dumps(candidates)}")

    for r in result.output.reasons:
        if r.client_id not in valid_ids:
            continue
        session.execute(
            text("update radar_output set reason = :reason where client_id = :id"),
            {"reason": r.reason.strip(), "id": r.client_id},
        )
    session.commit()
