"""The six tool implementations — plain functions over the engine + DB.

Each maps 1:1 to a Copilot tool (IMPLEMENTATION.md §7) and hits the same logic the REST
endpoints do. They take a live `Session` (and, where the engine is involved, a resolved
`MarketModel`) and return JSON-serialisable dicts — the same payload that is both fed back
to the LLM and surfaced to the frontend as the visible tool-call result.

None of these are open-ended: the book queries read the cached `radar_output` /
`latest_baseline`; what-if and stress run the Monte Carlo engine live; add_transactions
*parses only* (LLM → structured rows) and never writes — a misparse must be caught before
it touches the ledger.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sim_kernel import pipelines
from sim_kernel.categories import CAT_INDEX
from sim_kernel.state import MarketModel
from sim_kernel.whatif import Levers

from ..config import settings
from ..engine.history import value_over_time
from ..engine.loader import load_client_state, load_client_states
from ..engine.radar_status import status as mismatch_status
from ..gpu import client as gpu_client

CATEGORY_MIN_WEIGHT = 0.15  # a client "has exposure to" a category above this weight
AMC_CONCENTRATION = 0.40  # single-fund-house weight above this is "overexposed" (matches
                           # the category concentration threshold in api/clients.py)
CALL_LIST_LIMIT = 25


# ── query_book ────────────────────────────────────────────────────────────────
def query_book(
    session: Session,
    off_track: bool | None = None,
    risk_profile: str | None = None,
    over_exposed: bool | None = None,
    category: str | None = None,
    over_concentrated_amc: bool | None = None,
    name: str | None = None,
    limit: int = CALL_LIST_LIMIT,
) -> dict:
    """Find clients matching criteria: off-track goals, over-exposed vs tolerance,
    a given risk profile, heavy exposure to one of the 14 categories, over-concentrated
    in a single fund house (AMC), or a name search (use `name` to resolve a client the
    advisor refers to by name into their client_id). Reads the cached radar/baseline —
    run the book analysis first if it comes back empty."""
    rows = session.execute(
        text(
            """
            select c.id, c.name, c.risk_profile,
                   r.suitability_mismatch, r.simulated_dd, r.tolerable_dd, r.flags,
                   coalesce(h.total, 0) as portfolio_value,
                   tc.category as top_category, tc.weight as top_weight,
                   cx.weight as cat_weight,
                   ta.amc as top_amc, ta.weight as top_amc_weight
            from clients c
            left join radar_output r on r.client_id = c.id
            left join (
                select client_id, sum(value) as total from latest_holdings group by client_id
            ) h on h.client_id = c.id
            left join lateral (
                select category,
                       sum(value) / nullif(
                           (select sum(value) from latest_holdings lh2 where lh2.client_id = c.id), 0
                       ) as weight
                from latest_holdings lh where lh.client_id = c.id
                group by category order by sum(value) desc limit 1
            ) tc on true
            left join lateral (
                select sum(value) / nullif(
                           (select sum(value) from latest_holdings lh3 where lh3.client_id = c.id), 0
                       ) as weight
                from latest_holdings lh
                where lh.client_id = c.id and lh.category = :category
            ) cx on true
            left join lateral (
                select f.amc,
                       sum(lh.value) / nullif(
                           (select sum(value) from latest_holdings lh4 where lh4.client_id = c.id), 0
                       ) as weight
                from latest_holdings lh
                join funds f on f.id = lh.fund_id
                where lh.client_id = c.id and f.amc is not null
                group by f.amc order by sum(lh.value) desc limit 1
            ) ta on true
            where (cast(:risk_profile as text) is null or c.risk_profile = :risk_profile)
              and (not :off_track or r.flags @> cast('["off_track"]' as jsonb))
              and (not :over_exposed or r.suitability_mismatch > 0)
              and (cast(:category as text) is null or coalesce(cx.weight, 0) >= :cat_min)
              and (not :over_concentrated_amc or coalesce(ta.weight, 0) > :amc_min)
              and (cast(:name as text) is null or c.name ilike '%' || :name || '%')
            order by r.suitability_mismatch desc nulls last, portfolio_value desc
            limit :limit
            """
        ),
        {
            "off_track": bool(off_track),
            "over_exposed": bool(over_exposed),
            "risk_profile": risk_profile,
            "category": category,
            "over_concentrated_amc": bool(over_concentrated_amc),
            "name": name,
            "cat_min": CATEGORY_MIN_WEIGHT,
            "amc_min": AMC_CONCENTRATION,
            "limit": limit,
        },
    ).mappings().all()

    matches = [
        {
            "client_id": r["id"],
            "name": r["name"],
            "risk_profile": r["risk_profile"],
            "portfolio_value": round(float(r["portfolio_value"])),
            "suitability_mismatch": _f(r["suitability_mismatch"]),
            "simulated_dd": abs(_f(r["simulated_dd"])) if r["simulated_dd"] is not None else None,
            "over_exposed": (r["suitability_mismatch"] or 0) > 0,
            "flags": r["flags"] or [],
            "top_category": r["top_category"],
            "top_weight": _f(r["top_weight"]),
            "category_weight": _f(r["cat_weight"]) if category else None,
            "top_amc": r["top_amc"],
            "top_amc_weight": _f(r["top_amc_weight"]),
        }
        for r in rows
    ]
    return {
        "count": len(matches),
        "criteria": _clean(
            off_track=off_track, risk_profile=risk_profile, over_exposed=over_exposed,
            category=category, over_concentrated_amc=over_concentrated_amc, name=name,
        ),
        "clients": matches,
    }


# ── get_client_brief ──────────────────────────────────────────────────────────
def get_client_brief(session: Session, client_id: int) -> dict:
    """Pull the latest per-client analysis: profile, goal probabilities, portfolio
    downside + suitability, top allocation, monthly SIP commitment, and concentration
    flags. Reads the baseline cache (fast); says so if the client hasn't been scored yet."""
    profile = session.execute(
        text("select id, name, age, risk_profile from clients where id = :id"),
        {"id": client_id},
    ).mappings().first()
    if profile is None:
        return {"error": f"client {client_id} not found"}

    baseline = session.execute(
        text(
            """
            select goals, var_95, cvar_95, max_drawdown, suitability_mismatch,
                   risk_score, as_of_date
            from latest_baseline where client_id = :id
            """
        ),
        {"id": client_id},
    ).mappings().first()

    radar = session.execute(
        text(
            "select suitability_mismatch, simulated_dd, tolerable_dd, flags "
            "from radar_output where client_id = :id"
        ),
        {"id": client_id},
    ).mappings().first()

    alloc_rows = session.execute(
        text(
            """
            select category, sum(value) as value
            from latest_holdings where client_id = :id
            group by category order by value desc
            """
        ),
        {"id": client_id},
    ).mappings().all()
    total = sum(float(r["value"]) for r in alloc_rows)
    allocation = [
        {"category": r["category"], "value": round(float(r["value"])),
         "weight": round(float(r["value"]) / total, 4) if total else 0.0}
        for r in alloc_rows[:5]
    ]

    # Forward-looking SIP schedule — total ₹/month + the biggest contributions.
    sip_rows = session.execute(
        text(
            """
            select f.name as fund_name, f.category, s.monthly_amount, s.stepup_rate
            from sip_schedule s join funds f on f.id = s.fund_id
            where s.client_id = :id and s.active
            order by s.monthly_amount desc
            """
        ),
        {"id": client_id},
    ).mappings().all()
    monthly_sip = sum(float(r["monthly_amount"]) for r in sip_rows)
    sips = [
        {"fund_name": r["fund_name"], "category": r["category"],
         "monthly_amount": round(float(r["monthly_amount"])),
         "stepup_rate": float(r["stepup_rate"] or 0)}
        for r in sip_rows[:5]
    ]

    brief: dict = {
        "client_id": profile["id"],
        "name": profile["name"],
        "age": profile["age"],
        "risk_profile": profile["risk_profile"],
        "portfolio_value": round(total),
        "top_allocation": allocation,
        "monthly_sip": round(monthly_sip),
        "sip_count": len(sip_rows),
        "sips": sips,
        "scored": baseline is not None,
    }
    if baseline is None:
        brief["note"] = "No baseline yet — run the book analysis to score this client."
        return brief

    tolerable = pipelines.TOLERABLE_DD.get(
        profile["risk_profile"] or "balanced", pipelines.TOLERABLE_DD["balanced"]
    )
    mismatch = _f(radar["suitability_mismatch"]) if radar else _f(baseline["suitability_mismatch"])
    brief.update(
        as_of_date=str(baseline["as_of_date"]),
        goals=[
            {
                "goal_id": g.get("goal_id"),
                "name": g.get("name"),
                "target_amount": g.get("target_amount"),
                "horizon_months": g.get("horizon_months"),
                "success_prob": g.get("success_prob"),
                "on_track": (g.get("success_prob") or 0) >= settings.mc_confidence,
                "shortfall_expected": (g.get("shortfall") or {}).get("expected"),
                "terminal_p50": (g.get("terminal_pcts") or {}).get("p50"),
            }
            for g in (baseline["goals"] or [])
        ],
        risk={
            "var_95": _f(baseline["var_95"]),
            "cvar_95": _f(baseline["cvar_95"]),
            "max_drawdown": _f(baseline["max_drawdown"]),
            "tolerable_dd": tolerable,
            "suitability_mismatch": mismatch,
            "over_exposed": (mismatch or 0) > 0,
            "risk_score": _f(baseline["risk_score"]),
        },
        flags=(radar["flags"] if radar else []) or [],
    )
    return brief


# ── run_whatif ────────────────────────────────────────────────────────────────
def run_whatif(
    session: Session,
    model: MarketModel,
    client_id: int,
    sip_delta: float | None = None,
    lump_sum: float | None = None,
    reallocate: dict | None = None,
    reduce_concentration: dict | None = None,
    horizon_shift: int | None = None,
    return_shock: dict | None = None,
) -> dict:
    """Re-simulate one client with a change and return the before/after diff: per-goal
    success probability + median terminal, and 1-year portfolio downside. Live Monte
    Carlo — the reported elapsed_ms/backend is the GPU-vs-CPU pitch number."""
    state = load_client_state(session, client_id, model)
    if state is None:
        return {"error": f"client {client_id} not found"}

    levers = Levers(
        sip_delta=float(sip_delta or 0),
        lump_sum=float(lump_sum or 0),
        reallocate=reallocate,
        reduce_concentration=reduce_concentration,
        horizon_shift=int(horizon_shift or 0),
        return_shock=return_shock,
    )
    return gpu_client.whatif(state, model, levers, n_paths=settings.mc_n_paths, seed=settings.mc_seed)


def _downsample(points: list, max_points: int = 40) -> list:
    """Thin a chronological list to at most `max_points`, always keeping the last one.
    Keeps a multi-year monthly history light enough that the LLM isn't handed a wall of
    numbers to (mis)transcribe — the chart itself always gets the full-resolution series
    from `value_over_time`, this only shrinks what rides along in the tool result."""
    if len(points) <= max_points:
        return points
    step = -(-len(points) // max_points)  # ceil division
    sampled = points[::step]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled


# ── project_portfolio ────────────────────────────────────────────────────────
def project_portfolio(
    session: Session,
    model: MarketModel,
    client_id: int,
    horizon_years: int = 10,
    sip_delta: float | None = None,
    lump_sum: float | None = None,
    reallocate: dict | None = None,
    reduce_concentration: dict | None = None,
    return_shock: dict | None = None,
) -> dict:
    """Project one client's portfolio value forward `horizon_years`, joined to their
    actual value-to-date: a solid history line plus a future P5/P50/P90 fan, optionally
    under a what-if lever (e.g. `sip_delta` for "what if they add ₹X more SIP"). This is
    the value-over-time chart; for goal-probability before/after, use `run_whatif`."""
    state = load_client_state(session, client_id, model)
    if state is None:
        return {"error": f"client {client_id} not found"}

    horizon_years = max(1, min(int(horizon_years or 10), 40))
    levers = Levers(
        sip_delta=float(sip_delta or 0), lump_sum=float(lump_sum or 0),
        reallocate=reallocate, reduce_concentration=reduce_concentration,
        return_shock=return_shock,
    )
    projection = gpu_client.project_portfolio(
        state, model, levers, horizon_months=horizon_years * 12,
        n_paths=settings.mc_n_paths, seed=settings.mc_seed,
    )

    history = value_over_time(session, client_id)
    hist_points = _downsample([{"date": str(p.date), "value": round(p.value)} for p in history])

    # Anchor the fan at the last historical point so it starts exactly where the solid
    # history line ends, in both date and value, instead of jumping to a fresh origin.
    anchor_date = history[-1].date if history else date.today()
    anchor_value = round(history[-1].value) if history else projection["start_value"]
    series = projection["series"]
    future_dates = [str(anchor_date + timedelta(days=365 * (m // 12))) for m in series["months"]]
    p50 = [round(v) for v in series["p50"]]
    p5 = [round(v) for v in series["p5"]]
    p90 = [round(v) for v in series["p90"]]

    return {
        "client_id": client_id,
        "client_name": state.name,
        # The only fields worth narrating — everything else below is chart data, already
        # rendered automatically as a graph; never read from history/projection in prose.
        "headline": {
            "current_value": anchor_value,
            "monthly_sip": projection["monthly_sip"],
            "horizon_years": horizon_years,
            "median_at_horizon": p50[-1] if p50 else anchor_value,
            "worst_at_horizon": p5[-1] if p5 else anchor_value,
            "best_at_horizon": p90[-1] if p90 else anchor_value,
        },
        "levers": projection["levers"],
        "horizon_years": horizon_years,
        "current_value": anchor_value,
        "monthly_sip": projection["monthly_sip"],
        "history": hist_points,
        "projection": {
            "dates": [str(anchor_date), *future_dates],
            "p5": [anchor_value, *p5],
            "p50": [anchor_value, *p50],
            "p90": [anchor_value, *p90],
        },
        "elapsed_ms": projection["elapsed_ms"],
        "backend": projection["backend"],
    }


# ── stress_book ───────────────────────────────────────────────────────────────
def stress_book(
    session: Session,
    model: MarketModel,
    shock: dict,
    filters: dict | None = None,
) -> dict:
    """Apply one market shock across the whole book and return who breaches their
    tolerance, worst first. `shock` is per-category deltas + optional horizon_months,
    e.g. {"high_risk_equity": -0.20, "horizon_months": 3}. Monte Carlo (correlated
    spillover via Σ) by default; set shock["monte_carlo"]=false for plain weight×shock
    arithmetic instead."""
    filters = filters or {}
    client_ids = None
    risk_profile = filters.get("risk_profile")

    states = load_client_states(session, model)
    if risk_profile:
        states = [s for s in states if s.risk_profile == risk_profile]

    result = gpu_client.book_stress(
        states, model, shock, n_paths=settings.mc_n_paths, seed=settings.mc_seed,
    )
    breaches = result["breaches"]

    names = {s.id: s.name for s in states}
    profiles = {s.id: s.risk_profile for s in states}
    ranked = [
        {
            "client_id": b["client_id"],
            "name": names.get(b["client_id"]),
            "risk_profile": profiles.get(b["client_id"]),
            "loss": b["loss"],
            "tolerable": b["tolerable"],
            "severity": b["severity"],
        }
        for b in breaches[:20]
    ]
    deltas = {k: v for k, v in shock.items() if k in CAT_INDEX}
    return {
        "shock": deltas,
        "horizon_months": int(shock.get("horizon_months", 0)) or None,
        "mode": "monte_carlo" ,
        "clients_evaluated": len([s for s in states if s.total > 0]),
        "breaches": len(breaches),
        "filters": _clean(**filters),
        "ranked": ranked,
    }


# ── rank_book ─────────────────────────────────────────────────────────────────
def rank_book(session: Session, limit: int = CALL_LIST_LIMIT) -> dict:
    """The suitability-mismatch call list across the whole book — who to call first and
    why, ranked by how far simulated downside exceeds tolerance."""
    rows = session.execute(
        text(
            """
            select c.id, c.name, c.risk_profile,
                   r.suitability_mismatch, r.simulated_dd, r.tolerable_dd, r.flags, r.reason, b.goals
            from radar_output r
            join clients c on c.id = r.client_id
            left join latest_baseline b on b.client_id = r.client_id
            order by r.suitability_mismatch desc nulls last
            limit :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()

    call_list = []
    for r in rows:
        goals = r["goals"] or []
        worst = min(goals, key=lambda g: g.get("success_prob", 1.0), default=None)
        off_track = sum(1 for g in goals if (g.get("success_prob") or 1.0) < 0.50)
        mismatch = _f(r["suitability_mismatch"])
        call_list.append({
            "client_id": r["id"],
            "name": r["name"],
            "risk_profile": r["risk_profile"],
            "suitability_mismatch": mismatch,
            "simulated_dd": abs(_f(r["simulated_dd"])) if r["simulated_dd"] is not None else None,
            "tolerable_dd": abs(_f(r["tolerable_dd"])) if r["tolerable_dd"] is not None else None,
            "status": mismatch_status(mismatch),
            "flags": r["flags"] or [],
            "worst_goal": worst.get("name") if worst else None,
            "worst_goal_prob": worst.get("success_prob") if worst else None,
            "off_track_goals": off_track,
            "reason": r["reason"],
        })
    return {"count": len(call_list), "call_list": call_list}


# ── rank_goal_shortfalls ────────────────────────────────────────────────────────
def rank_goal_shortfalls(session: Session, limit: int = CALL_LIST_LIMIT) -> dict:
    """Every off-track goal across the whole book, ranked by expected ₹ shortfall —
    "who's furthest from their goal, and by when" rather than rank_book's suitability
    (risk-direction) ranking. Reads the same cached `latest_baseline.goals` per-goal
    stats `get_client_brief` reads for one client, just unnested across the whole book."""
    rows = session.execute(
        text(
            """
            select c.id, c.name, b.goals
            from latest_baseline b
            join clients c on c.id = b.client_id
            where b.goals is not null
            """
        )
    ).mappings().all()

    shortfalls = []
    for r in rows:
        for g in (r["goals"] or []):
            prob = g.get("success_prob")
            if prob is None or prob >= settings.mc_confidence:
                continue
            sf = g.get("shortfall") or {}
            shortfalls.append({
                "client_id": r["id"],
                "name": r["name"],
                "goal_id": g.get("goal_id"),
                "goal_name": g.get("name"),
                "target_amount": g.get("target_amount"),
                "horizon_months": g.get("horizon_months"),
                "success_prob": prob,
                "shortfall_expected": sf.get("expected"),
                "shortfall_worst": sf.get("worst_p5"),
            })
    shortfalls.sort(key=lambda x: x["shortfall_expected"] or 0, reverse=True)
    ranked = shortfalls[:limit]
    return {"count": len(shortfalls), "ranked": ranked}


# ── get_book_insights ─────────────────────────────────────────────────────────
def get_book_insights(session: Session) -> dict:
    """The cached AI-generated book narrative (morning briefing + insight cards) for the
    latest scored day — the same thing the dashboard shows. Ground answers about "what
    stood out" or "why did you flag X" in this instead of re-deriving it from scratch."""
    from ..llm.insights import strip_emphasis_tags

    latest = session.execute(text("select max(as_of_date) as d from book_insights")).scalar()
    if latest is None:
        return {"as_of_date": None, "briefing": None, "insights": []}

    rows = session.execute(
        text(
            "select kind, severity, title, body, client_ids from book_insights "
            "where as_of_date = :d order by id"
        ),
        {"d": latest},
    ).mappings().all()
    briefing = strip_emphasis_tags(next((r["body"] for r in rows if r["kind"] == "briefing"), None))
    insights = [
        {
            "kind": r["kind"], "severity": r["severity"], "title": r["title"],
            "body": r["body"], "client_ids": r["client_ids"] or [],
        }
        for r in rows if r["kind"] != "briefing"
    ]
    return {"as_of_date": str(latest), "briefing": briefing, "insights": insights}


# ── book_trend ────────────────────────────────────────────────────────────────
def book_trend(session: Session, lookback_days: int = 30) -> dict:
    """Breach/watch/ok counts over the last scored days, plus who changed status between
    the latest two runs — answers "is my book getting riskier" and "who newly needs a call
    since last time" using the append-only radar_snapshots history."""
    dates = session.execute(
        text(
            "select distinct as_of_date from radar_snapshots "
            "order by as_of_date desc limit :n"
        ),
        {"n": max(2, lookback_days)},
    ).scalars().all()
    dates = list(reversed(dates))

    points = []
    for d in dates:
        rows = session.execute(
            text("select suitability_mismatch from radar_snapshots where as_of_date = :d"),
            {"d": d},
        ).scalars().all()
        counts = {"breach": 0, "watch": 0, "ok": 0}
        for m in rows:
            counts[mismatch_status(_f(m))] += 1
        points.append({"as_of_date": str(d), **counts})

    movers = []
    if len(dates) >= 2:
        current, prior = dates[-1], dates[-2]
        rows = session.execute(
            text(
                """
                select c.name, cur.suitability_mismatch as cur_m, prev.suitability_mismatch as prev_m
                from radar_snapshots cur
                join radar_snapshots prev
                  on prev.client_id = cur.client_id and prev.as_of_date = :prior
                join clients c on c.id = cur.client_id
                where cur.as_of_date = :current
                """
            ),
            {"current": current, "prior": prior},
        ).mappings().all()
        rank = {"ok": 0, "watch": 1, "breach": 2}
        for r in rows:
            before = mismatch_status(_f(r["prev_m"]))
            after = mismatch_status(_f(r["cur_m"]))
            if before != after:
                movers.append({
                    "name": r["name"], "from_status": before, "to_status": after,
                    "direction": "worsened" if rank[after] > rank[before] else "improved",
                })

    return {"points": points, "movers": movers}


# ── add_transactions (parse only — never writes) ──────────────────────────────
def build_transaction_proposal(
    session: Session, client_id: int, raw_text: str, parsed: list[dict]
) -> dict:
    """Turn LLM-parsed rows into confirmable proposals: fund-name match → NAV lookup →
    units. Never writes — a misparse must be caught before it corrupts holdings. The LLM
    parse itself is done by the caller (async, on the Copilot's loop) and passed in."""
    name = session.execute(
        text("select name from clients where id = :id"), {"id": client_id}
    ).scalar()
    if name is None:
        return {"error": f"client {client_id} not found"}

    proposed = [_resolve_transaction(session, p) for p in parsed]
    matched = [r for r in proposed if r.get("matched")]
    return {
        "client_id": client_id,
        "client_name": name,
        "raw_text": raw_text,
        "proposed": proposed,
        "ready_to_commit": len(matched),
        "needs_review": len(proposed) - len(matched),
        "note": "Nothing has been written. Confirm the rows to commit them to the ledger.",
    }


_FUND_STOPWORDS = {"fund", "the", "into", "and", "for", "some", "units", "scheme",
                   "mutual", "an", "of", "fof", "etf"}


def _resolve_transaction(session: Session, p: dict) -> dict:
    """Match a parsed row's fund text to a real fund + NAV; compute units. Marks
    `matched=False` (with candidates) when the fund name is ambiguous or unknown.

    Matching is token-based: split the advisor's phrasing ("HDFC gold fund") into
    significant words, pull funds whose name/AMC contains any of them, then rank by how
    many tokens hit. This tolerates loose phrasing that a single substring match misses."""
    query = (p.get("fund") or "").strip()
    amount = float(p.get("amount") or 0)
    txn_type = p.get("type") if p.get("type") in ("buy", "redeem") else "buy"
    txn_date = p.get("date") or str(date.today())

    row: dict = {
        "fund_query": query, "type": txn_type, "amount": round(amount),
        "date": txn_date, "matched": False,
    }

    tokens = [t for t in query.lower().split() if len(t) >= 3 and t not in _FUND_STOPWORDS]
    if not tokens:
        row["note"] = "no matching fund found"
        return row

    patterns = [f"%{t}%" for t in tokens]
    rows = session.execute(
        text(
            """
            select id, name, amc, category from funds
            where lower(name) like any(:pats) or lower(coalesce(amc,'')) like any(:pats)
            limit 40
            """
        ),
        {"pats": patterns},
    ).mappings().all()
    if not rows:
        row["note"] = "no matching fund found"
        return row

    def _score(r) -> tuple:
        hay = f"{r['name']} {r['amc'] or ''}".lower()
        hits = sum(1 for t in tokens if t in hay)
        return (hits, -len(r["name"]))  # most tokens matched, then shortest name

    candidates = sorted(rows, key=_score, reverse=True)[:5]
    best = candidates[0]
    nav = session.execute(
        text(
            """
            select nav from nav_history
            where fund_id = :fid and date <= :d
            order by date desc limit 1
            """
        ),
        {"fid": best["id"], "d": txn_date},
    ).scalar()
    if nav is None:
        nav = session.execute(
            text("select nav from nav_history where fund_id = :fid order by date desc limit 1"),
            {"fid": best["id"]},
        ).scalar()

    row.update(
        matched=True,
        fund_id=best["id"], fund_name=best["name"], amc=best["amc"], category=best["category"],
        nav=round(float(nav), 4) if nav is not None else None,
        units=round(amount / float(nav), 4) if nav else None,
    )
    if len(candidates) > 1:
        row["alternatives"] = [
            {"fund_id": c["id"], "fund_name": c["name"]} for c in candidates[1:]
        ]
    return row


# ── run_sql (read-only, LLM-authored) ──────────────────────────────────────────
SQL_ROW_LIMIT = 200
_SQL_ALLOWED_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|call|copy|"
    r"vacuum|reindex|comment|execute|do|listen|notify|lock)\b",
    re.IGNORECASE,
)


def run_sql(session: Session, query: str) -> dict:
    """Run one ad-hoc, read-only SQL query against the book's schema for questions the
    other tools don't cover. Guardrails, not a substitute for care: must be a single
    SELECT/WITH statement (no semicolon-separated second statement), no DDL/DML/session
    keywords, a 5s statement timeout, and results capped at SQL_ROW_LIMIT rows (wrapped
    as a subquery so the cap applies regardless of the query's own ORDER BY/LIMIT)."""
    q = query.strip().rstrip(";")
    if ";" in q:
        return {"error": "only a single statement is allowed — remove the semicolon", "query": q}
    if not _SQL_ALLOWED_START.match(q):
        return {"error": "only SELECT/WITH queries are allowed", "query": q}
    if _SQL_FORBIDDEN.search(q):
        return {"error": "query contains a disallowed keyword — only reads are allowed", "query": q}

    try:
        session.execute(text("set local statement_timeout = '5000ms'"))
        rows = session.execute(
            # the newlines around `q` keep a trailing `-- comment` in the query from
            # swallowing the closing paren/limit clause below it
            text(f"select * from (\n{q}\n) as _sub limit :lim"), {"lim": SQL_ROW_LIMIT + 1}
        ).mappings().all()
    except SQLAlchemyError as e:
        session.rollback()
        return {"error": str(getattr(e, "orig", e)), "query": q}

    truncated = len(rows) > SQL_ROW_LIMIT
    rows = rows[:SQL_ROW_LIMIT]
    return {
        "query": q,
        "row_count": len(rows),
        "truncated": truncated,
        "columns": list(rows[0].keys()) if rows else [],
        "rows": [{k: _jsonable(v) for k, v in r.items()} for r in rows],
    }


# ── helpers ───────────────────────────────────────────────────────────────────
def _f(x) -> float | None:
    return round(float(x), 4) if x is not None else None


def _jsonable(v):
    """Coerce one arbitrary SQL result value (Decimal/date/datetime pass through
    unscathed everywhere else in this file because each query casts explicitly; `run_sql`
    can't know its columns ahead of time, so it coerces generically instead)."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _clean(**kw) -> dict:
    """Drop None-valued keys so echoed criteria/filters stay tidy."""
    return {k: v for k, v in kw.items() if v is not None}
