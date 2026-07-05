"""Book-wide analytics — the non-AI half of the Risk Radar page.

Aggregate reads over the whole book: AUM, client/goal counts, risk-profile
breakdown, and the book-wide category allocation. The MC-driven suitability radar
(baseline_runs / radar_output) is a later phase; this ships the analytics that the
seeded tables already support.
"""

from __future__ import annotations

import logging
import statistics
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..engine.radar_status import status as mismatch_status
from ..gpu.client import backend_label
from ..schemas import (
    BookInsightItem,
    BookInsightsResponse,
    BookSummary,
    BookTrendResponse,
    CategoryAllocation,
    HeatmapCell,
    HeatmapRow,
    HistBucket,
    MoverRow,
    RadarCallRow,
    RadarKpis,
    RadarResponse,
    RiskProfileCount,
    ScatterPoint,
    TrendPoint,
)

router = APIRouter(prefix="/book", tags=["book"])
log = logging.getLogger(__name__)


@router.get("/summary", response_model=BookSummary)
def book_summary(session: Session = Depends(get_session)) -> BookSummary:
    total_clients = session.execute(text("select count(*) from clients")).scalar_one()
    total_goals = session.execute(text("select count(*) from goals")).scalar_one()
    avg_age = session.execute(text("select avg(age) from clients")).scalar_one()
    total_aum = session.execute(
        text("select coalesce(sum(value), 0) from latest_holdings")
    ).scalar_one()

    profile_rows = session.execute(
        text("select risk_profile, count(*) as n from clients group by risk_profile")
    ).mappings()
    counts = RiskProfileCount()
    for r in profile_rows:
        if r["risk_profile"] in ("conservative", "balanced", "aggressive"):
            setattr(counts, r["risk_profile"], r["n"])

    alloc_rows = session.execute(
        text(
            """
            select category, sum(value) as value
            from latest_holdings
            group by category
            order by value desc
            """
        )
    ).mappings().all()
    book_total = sum(float(r["value"]) for r in alloc_rows)
    allocation = [
        CategoryAllocation(
            category=r["category"],
            value=float(r["value"]),
            weight=(float(r["value"]) / book_total) if book_total else 0.0,
        )
        for r in alloc_rows
    ]

    return BookSummary(
        total_clients=total_clients,
        total_aum=float(total_aum),
        total_goals=total_goals,
        avg_age=float(avg_age) if avg_age is not None else None,
        by_risk_profile=counts,
        allocation=allocation,
    )


# ── Risk Radar ────────────────────────────────────────────────────────────────
# Drawdown-bucket columns (loss magnitudes) and their midpoints, used to tint each
# heatmap cell relative to a profile's tolerance. A cell is 'ok' if its typical loss is
# within tolerance, 'tight' if up to 10pts beyond, 'breach' otherwise.
_HEATMAP_COLUMNS = ["0 to −10%", "−10 to −20%", "−20 to −30%", "worse than −30%"]
_BUCKET_MIDS = [0.05, 0.15, 0.25, 0.37]
_TOLERABLE = {"conservative": 0.10, "balanced": 0.20, "aggressive": 0.35}
_CALL_LIST_LIMIT = 25


def _bucket_index(dd_magnitude: float) -> int:
    if dd_magnitude < 0.10:
        return 0
    if dd_magnitude < 0.20:
        return 1
    if dd_magnitude < 0.30:
        return 2
    return 3


def _cell_state(profile: str, bucket: int) -> str:
    tol = _TOLERABLE.get(profile, 0.20)
    mid = _BUCKET_MIDS[bucket]
    if mid <= tol:
        return "ok"
    if mid <= tol + 0.10:
        return "tight"
    return "breach"


@router.get("/radar", response_model=RadarResponse)
def book_radar(session: Session = Depends(get_session)) -> RadarResponse:
    """The Monte Carlo suitability radar: KPIs, the profile×drawdown heatmap, the
    goal-success distribution, and the ranked priority call list. Reads the cached
    `radar_output` + `latest_baseline` (filled by the book-analysis run) — no live sim."""
    # Latest baseline meta (as-of date + paths). Empty if the analysis hasn't run.
    meta = session.execute(
        text("select max(as_of_date) as d, max(n_paths) as n from baseline_runs")
    ).mappings().first()
    as_of = meta["d"] if meta else None
    n_paths = int(meta["n"]) if meta and meta["n"] else None

    # One row per client: radar_output (dd/flags) + latest_baseline (goals/VaR).
    rows = session.execute(
        text(
            """
            select c.id, c.name, c.risk_profile,
                   r.suitability_mismatch, r.tolerable_dd, r.simulated_dd, r.flags, r.reason,
                   b.goals, b.var_95, b.cvar_95,
                   coalesce(h.total, 0) as portfolio_value
            from radar_output r
            join clients c on c.id = r.client_id
            left join latest_baseline b on b.client_id = r.client_id
            left join (
                select client_id, sum(value) as total from latest_holdings group by client_id
            ) h on h.client_id = c.id
            """
        )
    ).mappings().all()

    clients_scored = len(rows)

    # Dominant category (the "driver") per client, computed once for the whole book.
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

    # ── KPIs + heatmap counts + goal histogram + scatter, in one pass ─────────
    mismatches = watch = off_track_clients = concentrated_clients = 0
    var_list: list[float] = []
    cvar_list: list[float] = []
    all_goal_probs: list[float] = []
    heat_counts = {p: [0, 0, 0, 0] for p in _TOLERABLE}
    scatter: list[ScatterPoint] = []

    for r in rows:
        mismatch = float(r["suitability_mismatch"] or 0)
        st = mismatch_status(mismatch)
        if st == "breach":
            mismatches += 1
        elif st == "watch":
            watch += 1

        flags = r["flags"] or []
        if "off_track" in flags:
            off_track_clients += 1
        if "concentrated_fund" in flags or "concentrated_category" in flags:
            concentrated_clients += 1

        if r["var_95"] is not None:
            var_list.append(float(r["var_95"]))
        if r["cvar_95"] is not None:
            cvar_list.append(float(r["cvar_95"]))

        profile = r["risk_profile"] or "balanced"
        if profile in heat_counts:
            heat_counts[profile][_bucket_index(abs(float(r["simulated_dd"] or 0)))] += 1

        goals = r["goals"] or []
        worst_prob = None
        for g in goals:
            if g.get("success_prob") is not None:
                p = float(g["success_prob"])
                all_goal_probs.append(p)
                worst_prob = p if worst_prob is None else min(worst_prob, p)

        scatter.append(ScatterPoint(
            client_id=r["id"],
            name=r["name"],
            risk_profile=r["risk_profile"] or "balanced",
            mismatch=mismatch,
            worst_goal_prob=worst_prob,
            portfolio_value=float(r["portfolio_value"] or 0),
        ))

    kpis = RadarKpis(
        mismatches=mismatches,
        watch=watch,
        median_goal_success=statistics.median(all_goal_probs) if all_goal_probs else None,
        book_var_95=statistics.median(var_list) if var_list else None,
        book_cvar_95=statistics.median(cvar_list) if cvar_list else None,
        off_track_clients=off_track_clients,
        concentrated_clients=concentrated_clients,
    )

    heatmap = [
        HeatmapRow(
            profile=p,
            tolerable_dd=_TOLERABLE[p],
            cells=[HeatmapCell(count=heat_counts[p][b], state=_cell_state(p, b)) for b in range(4)],
        )
        for p in ("conservative", "balanced", "aggressive")
    ]

    # Goal-success distribution histogram.
    hist_edges = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.01)]
    hist_labels = ["0–20%", "20–40%", "40–60%", "60–80%", "80–95%", "95–100%"]
    hist = [0] * len(hist_edges)
    for p in all_goal_probs:
        for i, (lo, hi) in enumerate(hist_edges):
            if lo <= p < hi:
                hist[i] += 1
                break
    goal_success_hist = [HistBucket(label=lbl, count=n) for lbl, n in zip(hist_labels, hist)]

    # ── Priority call list — ranked by how far downside exceeds tolerance ─────
    ranked = sorted(rows, key=lambda r: float(r["suitability_mismatch"] or 0), reverse=True)
    call_list: list[RadarCallRow] = []
    for r in ranked[:_CALL_LIST_LIMIT]:
        mismatch = float(r["suitability_mismatch"] or 0)
        status = mismatch_status(mismatch)

        goals = r["goals"] or []
        worst = min(goals, key=lambda g: g.get("success_prob", 1.0), default=None)
        off_track_goals = sum(1 for g in goals if g.get("success_prob", 1.0) < 0.50)
        cat, weight = top_cat.get(r["id"], (None, 0.0))

        call_list.append(RadarCallRow(
            client_id=r["id"],
            name=r["name"],
            risk_profile=r["risk_profile"] or "balanced",
            tolerable_dd=abs(float(r["tolerable_dd"] or 0)),
            simulated_dd=abs(float(r["simulated_dd"] or 0)),
            mismatch=mismatch,
            portfolio_value=float(r["portfolio_value"] or 0),
            flags=r["flags"] or [],
            top_category=cat,
            top_weight=weight,
            worst_goal_name=worst.get("name") if worst else None,
            worst_goal_prob=worst.get("success_prob") if worst else None,
            off_track_goals=off_track_goals,
            status=status,
            reason=r["reason"],
        ))

    return RadarResponse(
        as_of_date=as_of,
        n_paths=n_paths,
        total_paths=clients_scored * (n_paths or 0),
        backend=backend_label(),
        market_source="derived" if settings.is_gpu_available else "fallback",
        clients_scored=clients_scored,
        heatmap_columns=_HEATMAP_COLUMNS,
        kpis=kpis,
        heatmap=heatmap,
        goal_success_hist=goal_success_hist,
        call_list=call_list,
        scatter=scatter,
    )


# ── AI book insights ────────────────────────────────────────────────────────────
@router.get("/insights", response_model=BookInsightsResponse)
def book_insights(session: Session = Depends(get_session)) -> BookInsightsResponse:
    """The cached LLM narrative for the latest scored day — the dashboard's morning
    briefing + insight cards. Empty payload (not an error) if none have been generated."""
    latest = session.execute(
        text("select max(as_of_date) as d from book_insights")
    ).scalar()
    if latest is None:
        return BookInsightsResponse(
            as_of_date=None, headline=None, briefing=None, insights=[],
            llm_configured=settings.llm_configured,
        )

    rows = session.execute(
        text(
            "select kind, severity, title, body, client_ids from book_insights "
            "where as_of_date = :d order by id"
        ),
        {"d": latest},
    ).mappings().all()
    briefing_row = next((r for r in rows if r["kind"] == "briefing"), None)
    insights = [
        BookInsightItem(
            kind=r["kind"], severity=r["severity"], title=r["title"] or "",
            body=r["body"], client_ids=r["client_ids"] or [],
        )
        for r in rows if r["kind"] != "briefing"
    ]
    return BookInsightsResponse(
        as_of_date=latest,
        headline=briefing_row["title"] if briefing_row else None,
        briefing=briefing_row["body"] if briefing_row else None,
        insights=insights,
        llm_configured=settings.llm_configured,
    )


@router.post("/insights/refresh", response_model=BookInsightsResponse)
async def refresh_book_insights(session: Session = Depends(get_session)) -> BookInsightsResponse:
    """Regenerate today's AI insights on demand, instead of waiting for the nightly run."""
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="AI insights unavailable: set GEMINI_API_KEY (or LLM_API_KEY) on the backend.",
        )
    from ..llm.call_reasons import generate_call_reasons
    from ..llm.insights import generate_book_insights

    await generate_book_insights(session, date.today())
    try:
        await generate_call_reasons(session)
    except Exception:
        log.exception("call reason generation failed")
    return book_insights(session)


# ── Book trend (risk migration over time) ───────────────────────────────────────
@router.get("/trend", response_model=BookTrendResponse)
def book_trend(session: Session = Depends(get_session)) -> BookTrendResponse:
    """Breach/watch/ok counts + AUM per scored day, plus who changed status between the
    latest two runs — "is my book getting riskier" and "who newly needs a call"."""
    dates = session.execute(
        text("select distinct as_of_date from radar_snapshots order by as_of_date desc limit 60")
    ).scalars().all()
    dates = list(reversed(dates))

    points: list[TrendPoint] = []
    for d in dates:
        rows = session.execute(
            text(
                "select suitability_mismatch, portfolio_value from radar_snapshots "
                "where as_of_date = :d"
            ),
            {"d": d},
        ).mappings().all()
        counts = {"breach": 0, "watch": 0, "ok": 0}
        aum = 0.0
        for r in rows:
            m = float(r["suitability_mismatch"]) if r["suitability_mismatch"] is not None else None
            counts[mismatch_status(m)] += 1
            aum += float(r["portfolio_value"] or 0)
        points.append(TrendPoint(
            as_of_date=d, breach=counts["breach"], watch=counts["watch"], ok=counts["ok"], aum=aum,
        ))

    movers: list[MoverRow] = []
    if len(dates) >= 2:
        current, prior = dates[-1], dates[-2]
        rows = session.execute(
            text(
                """
                select c.id, c.name, cur.suitability_mismatch as cur_m,
                       prev.suitability_mismatch as prev_m
                from radar_snapshots cur
                join radar_snapshots prev
                  on prev.client_id = cur.client_id and prev.as_of_date = :prior
                join clients c on c.id = cur.client_id
                where cur.as_of_date = :current
                """
            ),
            {"current": current, "prior": prior},
        ).mappings().all()
        # 'breach' > 'watch' > 'ok' — a move to a higher-severity status is a worsening.
        rank = {"ok": 0, "watch": 1, "breach": 2}
        for r in rows:
            before = mismatch_status(float(r["prev_m"]) if r["prev_m"] is not None else None)
            after = mismatch_status(float(r["cur_m"]) if r["cur_m"] is not None else None)
            if before == after:
                continue
            movers.append(MoverRow(
                client_id=r["id"], name=r["name"],
                direction="worsened" if rank[after] > rank[before] else "improved",
                from_status=before, to_status=after,
            ))

    return BookTrendResponse(points=points, movers=movers)
