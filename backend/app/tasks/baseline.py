"""The deterministic book analysis — "Run book analysis".

One pass over the whole book, no LLM anywhere: resolve the market model (derived when
GPU compute is configured, fallback otherwise), persist it, then run every client through
the Monte Carlo engine and cache the results. Produces:

* `assumptions` + `covariances` — the market model the book was scored on,
* `baseline_runs` — one dated row per client (goal probabilities, VaR/CVaR, drawdown,
  suitability) — append-only so "since last time" has history,
* `radar_output` — the current ranked suitability/concentration list (the Risk Radar).

Same seed + same data -> identical numbers. This is what `seed.py` runs to populate a
fresh DB and what the nightly job re-runs.

The whole book's `simulate()` calls happen in **one** GPU job (`app/gpu/client.py:
book_analysis`) — one async RunPod round trip (or one in-process call locally), not one
per client. `fund_value`/`category_value` (for concentration flags) never leave this
process; they're joined back onto the job's per-client results below.
"""

import json
from datetime import date

from sqlalchemy import text

from sim_kernel import pipelines

from ..config import settings
from ..db import SessionLocal
from ..engine import market
from ..engine.loader import load_client_states
from ..gpu import client as gpu_client

OFF_TRACK_PROB = 0.50  # a goal under this success probability is "off track"


def _finish_client(client_result: dict, state, as_of: date, seed: int, n_paths: int) -> dict:
    """Join a job's per-client numbers with the concentration/off-track flags that only
    the backend (holding fund_value/category_value) can compute."""
    goals = client_result["goals"]
    any_off_track = any((g["success_prob"] or 0) < OFF_TRACK_PROB for g in goals)

    total = state.total
    simulated_dd = client_result["max_drawdown"]
    mismatch = pipelines.suitability_mismatch(simulated_dd, state.risk_profile)
    tolerable = pipelines.TOLERABLE_DD.get(state.risk_profile, pipelines.TOLERABLE_DD["balanced"])

    flags = pipelines.concentration_flags(state.fund_value, state.category_value, total)
    if any_off_track:
        flags.append("off_track")

    return {
        "client_id": state.id,
        "as_of_date": as_of,
        "seed": seed,
        "n_paths": n_paths,
        "goals": goals,
        "var_95": client_result["var_95"],
        "cvar_95": client_result["cvar_95"],
        "max_drawdown": simulated_dd,  # positive magnitude
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

        job = gpu_client.book_analysis(
            states, model, n_paths=settings.mc_n_paths, seed=settings.mc_seed,
        )
        print(
            f"[baseline] engine done: backend={job['backend']} "
            f"elapsed_ms={job['elapsed_ms']}"
        )
        states_by_id = {s.id: s for s in states}

        n_goals = 0
        for i, client_result in enumerate(job["clients"], 1):
            state = states_by_id[client_result["client_id"]]
            r = _finish_client(client_result, state, as_of, job["seed"], job["n_paths"])
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
            "n_paths": job["n_paths"],
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
