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

from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..engine import pipelines, whatif
from ..engine.backend import BACKEND
from ..engine.categories import CAT_INDEX
from ..engine.loader import load_client_state, load_client_states
from ..engine.market import MarketModel
from ..engine.montecarlo import timer

CATEGORY_MIN_WEIGHT = 0.15  # a client "has exposure to" a category above this weight
CALL_LIST_LIMIT = 25


# ── query_book ────────────────────────────────────────────────────────────────
def query_book(
    session: Session,
    off_track: bool | None = None,
    risk_profile: str | None = None,
    over_exposed: bool | None = None,
    category: str | None = None,
    name: str | None = None,
    limit: int = CALL_LIST_LIMIT,
) -> dict:
    """Find clients matching criteria: off-track goals, over-exposed vs tolerance,
    a given risk profile, heavy exposure to one of the 14 categories, or a name search
    (use `name` to resolve a client the advisor refers to by name into their client_id).
    Reads the cached radar/baseline — run the book analysis first if it comes back empty."""
    rows = session.execute(
        text(
            """
            select c.id, c.name, c.risk_profile,
                   r.suitability_mismatch, r.simulated_dd, r.tolerable_dd, r.flags,
                   coalesce(h.total, 0) as portfolio_value,
                   tc.category as top_category, tc.weight as top_weight,
                   cx.weight as cat_weight
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
            where (cast(:risk_profile as text) is null or c.risk_profile = :risk_profile)
              and (not :off_track or r.flags @> cast('["off_track"]' as jsonb))
              and (not :over_exposed or r.suitability_mismatch > 0)
              and (cast(:category as text) is null or coalesce(cx.weight, 0) >= :cat_min)
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
            "name": name,
            "cat_min": CATEGORY_MIN_WEIGHT,
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
        }
        for r in rows
    ]
    return {
        "count": len(matches),
        "criteria": _clean(
            off_track=off_track, risk_profile=risk_profile,
            over_exposed=over_exposed, category=category, name=name,
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

    levers = whatif.Levers(
        sip_delta=float(sip_delta or 0),
        lump_sum=float(lump_sum or 0),
        reallocate=reallocate,
        reduce_concentration=reduce_concentration,
        horizon_shift=int(horizon_shift or 0),
        return_shock=return_shock,
    )
    with timer() as elapsed:
        result = whatif.run_whatif(state, model, levers, settings.mc_n_paths, settings.mc_seed)
    result.update(
        backend=BACKEND, n_paths=settings.mc_n_paths, seed=settings.mc_seed,
        elapsed_ms=round(elapsed() * 1000, 1),
    )
    return result


# ── stress_book ───────────────────────────────────────────────────────────────
def stress_book(
    session: Session,
    model: MarketModel,
    shock: dict,
    filters: dict | None = None,
) -> dict:
    """Apply one market shock across the whole book and return who breaches their
    tolerance, worst first. `shock` is per-category deltas + optional horizon_months,
    e.g. {"high_risk_equity": -0.20, "horizon_months": 3}. Deterministic weight×shock
    arithmetic by default; set shock["monte_carlo"]=true for correlated spillover via Σ."""
    filters = filters or {}
    client_ids = None
    risk_profile = filters.get("risk_profile")

    states = load_client_states(session, model)
    if risk_profile:
        states = [s for s in states if s.risk_profile == risk_profile]

    deterministic = not bool(shock.get("monte_carlo") or shock.get("mc"))
    breaches = pipelines.stress_book(states, shock, deterministic=deterministic)

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
        "mode": "monte_carlo" if not deterministic else "deterministic",
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
            "status": "breach" if (mismatch or 0) > 0 else "watch" if (mismatch or 0) > -0.05 else "ok",
            "flags": r["flags"] or [],
            "worst_goal": worst.get("name") if worst else None,
            "worst_goal_prob": worst.get("success_prob") if worst else None,
            "off_track_goals": off_track,
        })
    return {"count": len(call_list), "call_list": call_list}


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


# ── helpers ───────────────────────────────────────────────────────────────────
def _f(x) -> float | None:
    return round(float(x), 4) if x is not None else None


def _clean(**kw) -> dict:
    """Drop None-valued keys so echoed criteria/filters stay tidy."""
    return {k: v for k, v in kw.items() if v is not None}
