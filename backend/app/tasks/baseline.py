"""The deterministic book analysis — "Run book analysis".

One pass over the whole book, no LLM anywhere: resolve the market model (derived on GPU,
fallback on CPU), persist it, then run every client through the Monte Carlo engine and
cache the results. Produces:

* `assumptions` + `covariances` — the market model the book was scored on,
* `baseline_runs` — one dated row per client (goal probabilities, VaR/CVaR, drawdown,
  suitability) — append-only so "since last time" has history,
* `radar_output` — the current ranked suitability/concentration list (the Risk Radar).

Same seed + same data -> identical numbers. This is what `seed.py` runs to populate a
fresh DB and what the nightly job re-runs on the GPU box.
"""

import json
from datetime import date

import numpy as np
from sqlalchemy import text

from ..config import settings
from ..db import SessionLocal
from ..engine import market, pipelines
from ..engine.loader import ClientState, load_client_states
from ..engine.montecarlo import simulate

RISK_HORIZON_MONTHS = 12  # VaR/CVaR/drawdown are measured over one year
OFF_TRACK_PROB = 0.50  # a goal under this success probability is "off track"


def _analyze_client(c: ClientState, model: market.MarketModel, as_of: date) -> dict:
    """Everything cached for one client: per-goal probabilities + portfolio risk."""
    n_paths, seed = settings.mc_n_paths, settings.mc_seed

    # Per-goal: simulate only the goal's sub-portfolio (its tagged funds), with its SIPs.
    goals_out = []
    any_off_track = False
    for g in c.goals:
        if g.target_amount <= 0:
            continue
        terminals = simulate(
            g.holdings,
            model.mu,
            model.L,
            g.monthly_sip,
            g.horizon_months,
            n_paths=n_paths,
            seed=seed,
            stepup_rate=g.stepup_rate,
        )
        prob = pipelines.goal_probability(terminals, g.target_amount)
        any_off_track = any_off_track or prob < OFF_TRACK_PROB
        goals_out.append(
            {
                "goal_id": g.goal_id,
                "name": g.name,
                "target_amount": g.target_amount,
                "horizon_months": g.horizon_months,
                "success_prob": round(prob, 4),
                "terminal_pcts": {
                    k: round(v) for k, v in pipelines.percentiles(terminals).items()
                },
                "shortfall": {
                    k: round(v)
                    for k, v in pipelines.shortfall(terminals, g.target_amount).items()
                },
            }
        )

    # Portfolio risk: 1-year downside of the CURRENT holdings (no new SIP), for VaR/CVaR.
    total = c.total
    if total > 0:
        risk_terminals = simulate(
            c.holdings,
            model.mu,
            model.L,
            np.zeros_like(c.holdings),
            RISK_HORIZON_MONTHS,
            n_paths=n_paths,
            seed=seed,
        )
        var, cvar = pipelines.var_cvar(risk_terminals, total)
        simulated_dd = pipelines.simulated_drawdown(risk_terminals, total)
    else:
        var = cvar = simulated_dd = 0.0

    mismatch = pipelines.suitability_mismatch(simulated_dd, c.risk_profile)
    tolerable = pipelines.TOLERABLE_DD.get(
        c.risk_profile, pipelines.TOLERABLE_DD["balanced"]
    )

    flags = pipelines.concentration_flags(c.fund_value, c.category_value, total)
    if any_off_track:
        flags.append("off_track")

    return {
        "client_id": c.id,
        "as_of_date": as_of,
        "seed": seed,
        "n_paths": n_paths,
        "goals": goals_out,
        "var_95": round(var, 4),
        "cvar_95": round(cvar, 4),
        "max_drawdown": round(simulated_dd, 4),  # positive magnitude
        "suitability_mismatch": round(mismatch, 4),  # >0 => over-exposed
        "risk_score": round(min(100.0, simulated_dd * 100)),
        "radar": {
            "simulated_dd": round(-simulated_dd, 4),  # stored as signed downside
            "tolerable_dd": round(-tolerable, 4),
            "flags": flags,
        },
    }


def run_book_analysis(session=None, as_of: date | None = None) -> dict:
    """Resolve + persist the market model, then score the whole book. Returns stats."""
    own_session = session is None
    session = session or SessionLocal()
    as_of = as_of or date.today()
    try:
        model = market.resolve_market_model(session)
        print(
            f"[baseline] market model: {model.source}"
            + (f" ({model.n_months}m history)" if model.n_months else "")
            + f" | paths={settings.mc_n_paths} seed={settings.mc_seed}"
        )
        market.persist(session, model)

        states = load_client_states(session, model, as_of=as_of)
        print(f"[baseline] scoring {len(states)} clients...")

        n_goals = 0
        for i, c in enumerate(states, 1):
            r = _analyze_client(c, model, as_of)
            n_goals += len(r["goals"])

            session.execute(
                _BASELINE_UPSERT,
                {
                    "client_id": r["client_id"],
                    "as_of_date": r["as_of_date"],
                    "seed": r["seed"],
                    "n_paths": r["n_paths"],
                    "goals": json.dumps(r["goals"]),
                    "var_95": r["var_95"],
                    "cvar_95": r["cvar_95"],
                    "max_drawdown": r["max_drawdown"],
                    "suitability_mismatch": r["suitability_mismatch"],
                    "risk_score": r["risk_score"],
                },
            )
            session.execute(
                _RADAR_UPSERT,
                {
                    "client_id": r["client_id"],
                    "suitability_mismatch": r["suitability_mismatch"],
                    "tolerable_dd": r["radar"]["tolerable_dd"],
                    "simulated_dd": r["radar"]["simulated_dd"],
                    "flags": json.dumps(r["radar"]["flags"]),
                },
            )
            if i % 50 == 0:
                print(f"  ...{i}/{len(states)}")

        session.commit()
        stats = {
            "as_of_date": as_of.isoformat(),
            "market_source": model.source,
            "clients": len(states),
            "goals": n_goals,
            "n_paths": settings.mc_n_paths,
        }
        print(f"[baseline] done: {stats}")
        return stats
    finally:
        if own_session:
            session.close()


_BASELINE_UPSERT = text("""
    insert into baseline_runs
      (client_id, as_of_date, seed, n_paths, goals, var_95, cvar_95,
       max_drawdown, suitability_mismatch, risk_score)
    values
      (:client_id, :as_of_date, :seed, :n_paths, cast(:goals as jsonb), :var_95, :cvar_95,
       :max_drawdown, :suitability_mismatch, :risk_score)
    on conflict (client_id, as_of_date) do update set
      seed = excluded.seed, n_paths = excluded.n_paths, goals = excluded.goals,
      var_95 = excluded.var_95, cvar_95 = excluded.cvar_95,
      max_drawdown = excluded.max_drawdown,
      suitability_mismatch = excluded.suitability_mismatch,
      risk_score = excluded.risk_score
""")

_RADAR_UPSERT = text("""
    insert into radar_output
      (client_id, suitability_mismatch, tolerable_dd, simulated_dd, flags, updated_at)
    values
      (:client_id, :suitability_mismatch, :tolerable_dd, :simulated_dd,
       cast(:flags as jsonb), now())
    on conflict (client_id) do update set
      suitability_mismatch = excluded.suitability_mismatch,
      tolerable_dd = excluded.tolerable_dd,
      simulated_dd = excluded.simulated_dd,
      flags = excluded.flags, updated_at = now()
""")


if __name__ == "__main__":
    run_book_analysis()
