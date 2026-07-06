"""Book-wide AI insights — a one-shot structured-output agent (mirrors parser.py's
pattern, not copilot.py's tool loop: this needs no tools, just a JSON-in/JSON-out call).

Turns aggregate book stats (never raw per-client PII dumps) into a headline paragraph +
a handful of insight cards, cached in `book_insights` so the dashboard reads it for free.
Called from tasks/baseline.py after each nightly run, and on-demand via
POST /book/insights/refresh.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..engine.radar_status import status as mismatch_status
from .provider import get_model

_INSTRUCTIONS = """
You are a portfolio-risk analyst writing a same-day briefing for a financial advisor who
manages a book of mutual-fund clients. You are given a JSON snapshot of aggregate,
already-computed book statistics — you do not have access to raw client records.

Ground every claim ONLY in the numbers given in the JSON. Never invent a figure, a client
name, or a client id that is not present in the input. If the input doesn't support a
particular kind of observation (e.g. no prior snapshot for a trend), skip it rather than
guessing.

INLINE EMPHASIS TAGS — use these inside `headline` and `briefing` (nowhere else) to make
the one or two numbers/words that matter pop visually, the way a magazine pull-quote mixes
weights and colors instead of one flat sentence. Wrap a short span (a number, or 1-4 words)
like `{{tag:span}}`:
  {{hero:₹53.79 Cr}}   the single most important figure — rendered large and bold
  {{good:zero breaches}}   a positive signal — rendered in green
  {{warn:18 on watch}}   needs attention soon — rendered in amber
  {{bad:3 in breach}}   urgent/negative — rendered in rose
  {{muted:across 173 clients}}   a de-emphasized aside/qualifier — rendered smaller
Use 2-5 tags total across `headline`+`briefing` combined — enough to give the text real
typographic rhythm, not so many it turns into confetti. Never tag a whole sentence, only
the load-bearing word or number. Never nest tags. Plain prose still carries most of the
text; tags punctuate it.

Write:
- `headline`: one short punchy line (under 14 words) — the single thing an advisor should
  notice first, using 1-2 emphasis tags on its key number(s). E.g. "Your book stands
  {{hero:₹53.79 Cr}} strong with {{good:zero breaches}} today."
- `briefing`: one short paragraph (2-4 sentences) filling in the rest of the picture —
  what changed since the last run if prior-run data is given, and the next most important
  thing after the headline. Plain language, no jargon, ₹ amounts in Indian style (₹1.2 Cr).
- `insights`: 3 to 6 cards, each a distinct, concrete observation worth acting on or
  knowing (concentration clustering, a cohort newly breaching tolerance, recurring
  off-track goals, a positive signal, an opportunity like idle cash). Each needs:
  kind (risk|concentration|goals|opportunity), severity (good|info|watch|critical), a short
  title (under 8 words), a 1-2 sentence body (plain text, no emphasis tags here), and
  client_ids — the specific client ids from the input that this observation is about. Pull
  them from the matching input field: `movers[].client_id` for status/trend cards,
  `concentration_hotspots[].client_ids` for concentration cards, and
  `recurring_off_track_goal_names[].client_ids` for goal cards. Always include the relevant
  ids when the input provides them; leave the list empty only for a genuinely book-wide card
  with no clients named in the input. Prefer fewer, sharper insights over generic filler.
""".strip()


class InsightItem(BaseModel):
    kind: str
    severity: str
    title: str
    body: str
    client_ids: list[int] = []


class InsightsPayload(BaseModel):
    headline: str
    briefing: str
    insights: list[InsightItem]


_VALID_KINDS = {"risk", "concentration", "goals", "opportunity"}
_VALID_SEVERITIES = {"good", "info", "watch", "critical"}

_TAG_RE = re.compile(r"\{\{(?:hero|good|warn|bad|muted):([^}]+)\}\}")


def strip_emphasis_tags(text: str | None) -> str | None:
    """Drop the {{tag:span}} presentation markup, leaving plain text — for surfaces
    (like the Copilot chat) that shouldn't echo dashboard-only styling syntax."""
    if text is None:
        return None
    return _TAG_RE.sub(r"\1", text)


@lru_cache(maxsize=1)
def _agent():
    from pydantic_ai import Agent

    return Agent(get_model(), output_type=InsightsPayload, instructions=_INSTRUCTIONS)


def _gather_book_context(session: Session) -> dict:
    """Pure aggregate SQL — no per-client raw dump. Feeds the LLM prompt."""
    dates = session.execute(
        text("select distinct as_of_date from radar_snapshots order by as_of_date desc limit 2")
    ).scalars().all()
    current_date = dates[0] if dates else None
    prior_date = dates[1] if len(dates) > 1 else None

    def _status_counts(as_of: date) -> dict:
        rows = session.execute(
            text("select suitability_mismatch from radar_snapshots where as_of_date = :d"),
            {"d": as_of},
        ).scalars().all()
        counts = {"breach": 0, "watch": 0, "ok": 0}
        for m in rows:
            counts[mismatch_status(float(m) if m is not None else None)] += 1
        return counts

    current_counts = _status_counts(current_date) if current_date else None
    prior_counts = _status_counts(prior_date) if prior_date else None

    movers: list[dict] = []
    if current_date and prior_date:
        rows = session.execute(
            text(
                """
                select c.name, cur.client_id, cur.suitability_mismatch as cur_m,
                       prev.suitability_mismatch as prev_m
                from radar_snapshots cur
                join radar_snapshots prev
                  on prev.client_id = cur.client_id and prev.as_of_date = :prior
                join clients c on c.id = cur.client_id
                where cur.as_of_date = :current
                """
            ),
            {"current": current_date, "prior": prior_date},
        ).mappings().all()
        for r in rows:
            before = mismatch_status(float(r["prev_m"]) if r["prev_m"] is not None else None)
            after = mismatch_status(float(r["cur_m"]) if r["cur_m"] is not None else None)
            if before != after:
                movers.append({
                    "client_id": r["client_id"], "name": r["name"],
                    "from_status": before, "to_status": after,
                })

    # Concentration: which clients are over the category-min weight, per category.
    concentration_rows = session.execute(
        text(
            """
            with tot as (select client_id, sum(value) as t from latest_holdings group by client_id)
            select lh.category, lh.client_id
            from latest_holdings lh join tot on tot.client_id = lh.client_id
            where lh.value / nullif(tot.t, 0) >= 0.30
            """
        )
    ).mappings().all()
    concentration_by_cat: dict[str, list[int]] = {}
    for r in concentration_rows:
        concentration_by_cat.setdefault(r["category"], []).append(r["client_id"])
    concentration_hotspots = sorted(
        concentration_by_cat.items(), key=lambda kv: len(kv[1]), reverse=True
    )[:5]

    # Off-track goal aggregates + which goal names recur + which clients they belong to.
    goal_rows = session.execute(
        text("select client_id, goals from latest_baseline")
    ).mappings().all()
    off_track_names: dict[str, int] = {}
    off_track_client_ids: dict[str, list[int]] = {}
    off_track_total = 0
    for row in goal_rows:
        for g in row["goals"] or []:
            if (g.get("success_prob") or 1.0) < 0.50:
                off_track_total += 1
                name = g.get("name") or "Unnamed goal"
                off_track_names[name] = off_track_names.get(name, 0) + 1
                off_track_client_ids.setdefault(name, []).append(row["client_id"])
    top_off_track_goals = sorted(off_track_names.items(), key=lambda kv: kv[1], reverse=True)[:5]

    summary = session.execute(
        text(
            """
            select count(*) as n, coalesce(sum(h.total), 0) as aum
            from clients c
            left join (select client_id, sum(value) as total from latest_holdings group by client_id) h
              on h.client_id = c.id
            """
        )
    ).mappings().first()

    return {
        "as_of_date": str(current_date) if current_date else None,
        "total_clients": summary["n"],
        "book_aum": float(summary["aum"]),
        "current_status_counts": current_counts,
        "prior_status_counts": prior_counts,
        "prior_as_of_date": str(prior_date) if prior_date else None,
        "movers": movers[:15],
        "concentration_hotspots": [
            {"category": cat, "client_count": len(ids), "client_ids": ids[:10]}
            for cat, ids in concentration_hotspots
        ],
        "off_track_goal_count": off_track_total,
        "recurring_off_track_goal_names": [
            {"name": n, "count": c, "client_ids": off_track_client_ids.get(n, [])[:10]}
            for n, c in top_off_track_goals
        ],
    }


def _client_pools(context: dict) -> dict[str, list[int]]:
    """Candidate client ids per insight kind, derived from the same context sent to the LLM.
    Used to backfill cards the model returned with an empty client_ids list."""
    def _dedup(ids: list[int]) -> list[int]:
        seen: dict[int, None] = {}
        for i in ids:
            seen.setdefault(i, None)
        return list(seen)

    movers = _dedup([m["client_id"] for m in context.get("movers", [])])
    concentration = _dedup([
        cid for h in context.get("concentration_hotspots", []) for cid in h.get("client_ids", [])
    ])
    goals = _dedup([
        cid for g in context.get("recurring_off_track_goal_names", []) for cid in g.get("client_ids", [])
    ])
    any_pool = _dedup(movers + concentration + goals)
    return {
        "risk": movers or any_pool,
        "concentration": concentration or any_pool,
        "goals": goals or any_pool,
        "opportunity": movers or any_pool,
        "_any": any_pool,
    }


async def generate_book_insights(session: Session, as_of: date) -> None:
    """Build the context, call the LLM, cache the result. Raises on failure — the caller
    (baseline task) decides whether that should be fatal."""
    context = _gather_book_context(session)
    prompt = (
        f"Today's date is {as_of.isoformat()}. Book snapshot:\n"
        f"{json.dumps(context, default=str)}"
    )
    result = await _agent().run(prompt)
    payload = result.output

    # Deterministic client-id backfill. The LLM frequently omits client_ids even when the
    # context names them, and book-wide cards legitimately have none — but the dashboard needs
    # something to drill into, so map each empty card to the relevant client pool by kind.
    pools = _client_pools(context)
    for item in payload.insights:
        if not item.client_ids:
            item.client_ids = pools.get(item.kind, pools["_any"])[:10]

    session.execute(text("delete from book_insights where as_of_date = :d"), {"d": as_of})
    session.execute(
        text(
            "insert into book_insights (as_of_date, kind, severity, title, body, client_ids) "
            "values (:d, 'briefing', 'info', :headline, :body, '[]'::jsonb)"
        ),
        {"d": as_of, "headline": payload.headline, "body": payload.briefing},
    )
    for item in payload.insights:
        kind = item.kind if item.kind in _VALID_KINDS else "risk"
        severity = item.severity if item.severity in _VALID_SEVERITIES else "info"
        session.execute(
            text(
                "insert into book_insights (as_of_date, kind, severity, title, body, client_ids) "
                "values (:d, :kind, :severity, :title, :body, cast(:client_ids as jsonb))"
            ),
            {
                "d": as_of, "kind": kind, "severity": severity,
                "title": item.title, "body": item.body,
                "client_ids": json.dumps(item.client_ids),
            },
        )
    session.commit()
