"""The RunPod job contract — the only functions the worker's `handler.py` calls.

Each job bundles a *whole business operation*, not a single `simulate()` call. That
matters: `client_insights` alone can involve a `simulate()` per goal plus a 20-iteration
`required_sip` bisection for off-track goals. Proxying `simulate()` 1:1 over the network
would turn one advisor page-load into 20+ round trips. Bundling the loop into one job
means one round trip in, one JSON dict out — same shape whether it ran in-process (local
dev, no RunPod configured) or on a RunPod GPU worker.

Every function here takes and returns plain JSON-serializable dicts (lists, not
ndarrays) — exactly what travels in an HTTP body. `app.gpu.client` on the backend side
builds these payloads from `ClientState`/`GoalState`/`MarketModel` and unpacks the results
back into what callers (api/clients.py, tools/impl.py, tasks/baseline.py) expect.
"""

from __future__ import annotations

import numpy as np

from . import pipelines
from .backend import BACKEND, GPU, gpu_free_bytes, timer
from .categories import CAT_INDEX
from .montecarlo import simulate, simulate_batch, simulate_series
from .state import ClientState, GoalState, MarketModel
from .whatif import Levers, _shocked_mu, _transform, run_whatif as _run_whatif

RISK_HORIZON_MONTHS = 12

# Whole-book jobs (book_analysis, book_stress) batch up to this many clients/goals into
# one simulate_batch() call — one set of GPU kernel launches instead of one per item.
MAX_BATCH = 100

# simulate_batch runs in float32 (see montecarlo.py); a (batch, n_paths, 14) block plus
# the handful of same-shaped buffers alive inside its month loop (the fresh random draw,
# the correlated-shocks result, ...) is roughly this many live tensors at peak.
_BYTES_PER_ELEMENT = 4
_LIVE_TENSORS = 4
_MEM_SAFETY = 0.5  # only ever plan to use half of *currently free* device memory


def _safe_batch_size(n_paths: int, requested: int = MAX_BATCH) -> int:
    """Shrink `requested` if the GPU doesn't actually have room for it at this `n_paths`
    — MAX_BATCH=100 is a ceiling, not a guarantee. Off-GPU (local numpy dev) there's no
    such ceiling, so `requested` is used as-is; the same book that OOMs a small GPU
    tier at 500k paths just runs slower, smaller-batched, on whatever GPU is actually
    attached."""
    free = gpu_free_bytes()
    if free is None:
        return requested
    per_item_bytes = n_paths * 14 * _BYTES_PER_ELEMENT * _LIVE_TENSORS
    budget = int(free * _MEM_SAFETY)
    return max(1, min(requested, budget // max(per_item_bytes, 1)))


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _allocation_weights(*candidates: np.ndarray) -> np.ndarray:
    """First non-empty 14-vector normalised to sum 1 — where new SIP money should go."""
    for vec in candidates:
        total = float(vec.sum())
        if total > 0:
            return vec / total
    n = len(candidates[0]) if candidates else 14
    return np.full(n, 1.0 / n)


def _goal_insight(g: GoalState, model: MarketModel, client_holdings: np.ndarray,
                   n_paths: int, required_sip_paths: int, seed: int, confidence: float) -> dict:
    terminals = simulate(
        g.holdings, model.mu, model.L, g.monthly_sip, g.horizon_months,
        n_paths=n_paths, seed=seed, stepup_rate=g.stepup_rate,
    )
    prob = pipelines.goal_probability(terminals, g.target_amount)
    pcts = pipelines.percentiles(terminals)
    sf = pipelines.shortfall(terminals, g.target_amount)
    on_track = prob >= confidence

    required = None
    if not on_track:
        weights = _allocation_weights(g.holdings, g.monthly_sip, client_holdings)
        required = pipelines.required_sip(
            g.holdings, model.mu, model.L, g.target_amount, g.horizon_months, weights,
            confidence=confidence, n_paths=required_sip_paths, seed=seed,
            stepup_rate=g.stepup_rate,
        )

    return {
        "goal_id": g.goal_id, "name": g.name, "target_amount": g.target_amount,
        "horizon_months": g.horizon_months, "funded_value": g.start_value,
        "success_prob": round(prob, 4),
        "p5": round(pcts["p5"]), "p50": round(pcts["p50"]), "p90": round(pcts["p90"]),
        "shortfall_expected": round(sf["expected"]), "shortfall_worst": round(sf["worst_p5"]),
        "current_sip": float(g.monthly_sip.sum()),
        "required_sip": round(required) if required is not None else None,
        "on_track": on_track,
    }


def _client_risk(state: ClientState, model: MarketModel, n_paths: int, seed: int) -> dict:
    total = state.total
    if total <= 0:
        return {"var_95": 0.0, "cvar_95": 0.0, "max_drawdown": 0.0}
    terminals = simulate(
        state.holdings, model.mu, model.L, np.zeros_like(state.holdings),
        RISK_HORIZON_MONTHS, n_paths=n_paths, seed=seed,
    )
    var, cvar = pipelines.var_cvar(terminals, total)
    drawdown = pipelines.simulated_drawdown(terminals, total)
    return {"var_95": round(var, 4), "cvar_95": round(cvar, 4), "max_drawdown": round(drawdown, 4)}


# ── job: client_insights (live, single client) ────────────────────────────────
def client_insights(payload: dict) -> dict:
    model = MarketModel.from_payload(payload["model"])
    state = ClientState.from_payload(payload["client"])
    n_paths = int(payload.get("n_paths") or 8000)
    required_sip_paths = int(payload.get("required_sip_paths") or n_paths)
    seed = int(payload.get("seed") or 42)
    confidence = float(payload.get("confidence") or 0.80)

    with timer() as elapsed:
        goals = [
            _goal_insight(g, model, state.holdings, n_paths, required_sip_paths, seed, confidence)
            for g in state.goals if g.target_amount > 0
        ]
        risk = _client_risk(state, model, n_paths, seed)

    return {
        "goals": goals, **risk,
        "n_paths": n_paths, "seed": seed,
        "backend": BACKEND, "gpu": GPU, "elapsed_ms": round(elapsed() * 1000, 1),
    }


# ── job: whatif (live, single client) ──────────────────────────────────────────
def whatif(payload: dict) -> dict:
    model = MarketModel.from_payload(payload["model"])
    state = ClientState.from_payload(payload["client"])
    levers = Levers.from_payload(payload.get("levers") or {})
    n_paths = int(payload.get("n_paths") or 8000)
    seed = int(payload.get("seed") or 42)

    with timer() as elapsed:
        result = _run_whatif(state, model, levers, n_paths, seed)

    result.update(
        backend=BACKEND, gpu=GPU, n_paths=n_paths, seed=seed,
        elapsed_ms=round(elapsed() * 1000, 1),
    )
    return result


# ── job: portfolio_projection (live, single client) ────────────────────────────
def portfolio_projection(payload: dict) -> dict:
    """Year-by-year P5/P50/P90 portfolio value, optionally under what-if levers — the
    "portfolio health over N years" / "what if they add ₹X more SIP" chart. Reuses the
    same lever transforms `run_whatif` applies to the whole portfolio (`_transform` with
    `share=1.0`, `_shocked_mu`), just fed into `simulate_series()` instead of `simulate()`
    so the answer is a value-over-time series, not a single before/after point."""
    model = MarketModel.from_payload(payload["model"])
    state = ClientState.from_payload(payload["client"])
    levers = Levers.from_payload(payload.get("levers") or {})
    horizon_months = max(int(payload.get("horizon_months") or 120), 1)
    n_paths = int(payload.get("n_paths") or 8000)
    seed = int(payload.get("seed") or 42)

    holdings, sip = _transform(state.holdings, state.monthly_sip, levers, 1.0)
    mu = _shocked_mu(model.mu, levers)

    with timer() as elapsed:
        checkpoint_months, values = simulate_series(
            holdings, mu, model.L, sip, horizon_months,
            n_paths=n_paths, seed=seed, stepup_rate=state.stepup_rate,
        )
        series = pipelines.percentile_series(checkpoint_months, values)

    return {
        "client_id": state.id,
        "start_value": round(float(holdings.sum())),
        "monthly_sip": round(float(sip.sum())),
        "levers": levers.describe(),
        "series": series,
        "n_paths": n_paths, "seed": seed,
        "backend": BACKEND, "gpu": GPU, "elapsed_ms": round(elapsed() * 1000, 1),
    }


# ── job: book_analysis (nightly, whole book) ───────────────────────────────────
def _goal_stat(g: GoalState, terminals) -> dict:
    prob = pipelines.goal_probability(terminals, g.target_amount)
    return {
        "goal_id": g.goal_id, "name": g.name, "target_amount": g.target_amount,
        "horizon_months": g.horizon_months, "success_prob": round(prob, 4),
        "terminal_pcts": {k: round(v) for k, v in pipelines.percentiles(terminals).items()},
        "shortfall": {
            k: round(v) for k, v in pipelines.shortfall(terminals, g.target_amount).items()
        },
    }


def book_analysis(payload: dict) -> dict:
    """`simulate_batch()` across the whole book instead of one `simulate()` per goal/
    client — up to `MAX_BATCH` items per GPU call. No DB here — the backend attaches
    `fund_value`/`category_value`/flags itself after getting this back, since those
    never left the server.

    Deliberately does NOT call `required_sip` (unlike `client_insights`) — the nightly
    pass never bisected in the original design either; doing so here would add a
    20-iteration search per off-track goal, times the whole book, to a batch job nobody
    is waiting on. `required_sip` stays a live-request-only cost.

    Batching needs every item in one GPU call to step forward in lockstep, so goals are
    grouped by `horizon_months` first (different goals almost always have different
    horizons) and each group is chunked at `MAX_BATCH`. Portfolio risk uses one fixed
    horizon for every client, so it batches directly with no grouping needed.
    """
    model = MarketModel.from_payload(payload["model"])
    states = [ClientState.from_payload(c) for c in payload["clients"]]
    n_paths = int(payload.get("n_paths") or 8000)
    seed = int(payload.get("seed") or 42)

    with timer() as elapsed:
        # ── goals, grouped by horizon so each batch call steps forward in lockstep ──
        by_horizon: dict[int, list[tuple[int, int, GoalState]]] = {}
        for ci, state in enumerate(states):
            for gi, g in enumerate(state.goals):
                if g.target_amount > 0:
                    by_horizon.setdefault(g.horizon_months, []).append((ci, gi, g))

        goals_by_client: list[list[tuple[int, dict]]] = [[] for _ in states]
        for horizon, items in by_horizon.items():
            for chunk in _chunks(items, _safe_batch_size(n_paths)):
                holdings = np.stack([g.holdings for _, _, g in chunk])
                sip = np.stack([g.monthly_sip for _, _, g in chunk])
                stepup = np.array([g.stepup_rate for _, _, g in chunk])
                terminals = simulate_batch(
                    holdings, model.mu, model.L, sip, horizon,
                    n_paths=n_paths, seed=seed, stepup_rate=stepup,
                )
                for row, (ci, gi, g) in enumerate(chunk):
                    goals_by_client[ci].append((gi, _goal_stat(g, terminals[row])))

        # ── portfolio risk — one shared horizon for every client, batch directly ──
        risk_by_client: dict[int, dict] = {}
        risk_items = [(ci, s) for ci, s in enumerate(states) if s.total > 0]
        for chunk in _chunks(risk_items, _safe_batch_size(n_paths)):
            holdings = np.stack([s.holdings for _, s in chunk])
            terminals = simulate_batch(
                holdings, model.mu, model.L, np.zeros_like(holdings), RISK_HORIZON_MONTHS,
                n_paths=n_paths, seed=seed,
            )
            for row, (ci, s) in enumerate(chunk):
                var, cvar = pipelines.var_cvar(terminals[row], s.total)
                risk_by_client[ci] = {
                    "var_95": round(var, 4), "cvar_95": round(cvar, 4),
                    "max_drawdown": round(pipelines.simulated_drawdown(terminals[row], s.total), 4),
                }

        out = []
        for ci, state in enumerate(states):
            goals = [d for _, d in sorted(goals_by_client[ci])]  # restore original goal order
            risk = risk_by_client.get(ci, {"var_95": 0.0, "cvar_95": 0.0, "max_drawdown": 0.0})
            out.append({"client_id": state.id, "goals": goals, **risk})

    return {
        "clients": out, "n_paths": n_paths, "seed": seed,
        "backend": BACKEND, "gpu": GPU, "elapsed_ms": round(elapsed() * 1000, 1),
    }


# ── job: book_stress, Monte Carlo mode only ───────────────────────────────────
# (the deterministic mode is instant weight×shock arithmetic — the backend runs that
# in-process itself and never calls out here; see app/tools/impl.py:stress_book and
# pipelines.stress_book). One shock -> one shared horizon for the whole book, so this
# batches directly (no horizon grouping needed, unlike book_analysis's goals).
def book_stress(payload: dict) -> dict:
    model = MarketModel.from_payload(payload["model"])
    n_paths = int(payload.get("n_paths") or 8000)
    seed = int(payload.get("seed") or 42)
    shock = payload["shock"]
    horizon = max(int(shock.get("horizon_months", 0)), 1)
    deltas = {CAT_INDEX[tag]: d for tag, d in shock.items() if tag in CAT_INDEX}
    raw_clients = [c for c in payload["clients"] if sum(c["holdings"]) > 0]

    breaches = []
    outcomes = []  # per-client outcome distribution — populated for every client, not just
                   # breaches, so an UPSIDE shock ("what if gold spikes") has numbers to show
    with timer() as elapsed:
        for chunk in _chunks(raw_clients, _safe_batch_size(n_paths)):
            holdings = np.array([c["holdings"] for c in chunk], dtype=float)
            terminals = simulate_batch(
                holdings, model.mu, model.L, np.zeros_like(holdings), horizon,
                n_paths=n_paths, seed=seed, shock={"month": 0, "deltas": deltas},
            )
            for row, c in enumerate(chunk):
                total = float(holdings[row].sum())
                p5, p50, p95 = (float(q) for q in np.quantile(terminals[row], [0.05, 0.5, 0.95]))
                expected = float(terminals[row].mean())
                # fractional change vs current value (+ = gain, − = loss)
                outcomes.append({
                    "client_id": c["id"],
                    "value": round(total, 2),
                    "expected_change": round((expected - total) / total, 4),
                    "median_change": round((p50 - total) / total, 4),
                    "downside_change": round((p5 - total) / total, 4),   # p5: worst case
                    "upside_change": round((p95 - total) / total, 4),    # p95: best case
                })
                loss = (total - p5) / total
                tol = pipelines.TOLERABLE_DD.get(
                    c.get("risk_profile") or "balanced", pipelines.TOLERABLE_DD["balanced"],
                )
                if loss > tol:
                    breaches.append({
                        "client_id": c["id"], "loss": round(loss, 4),
                        "tolerable": tol, "severity": round(loss - tol, 4),
                    })

    breaches.sort(key=lambda b: b["severity"], reverse=True)
    return {
        "breaches": breaches, "outcomes": outcomes, "n_paths": n_paths, "seed": seed,
        "backend": BACKEND, "gpu": GPU, "elapsed_ms": round(elapsed() * 1000, 1),
    }


JOBS = {
    "client_insights": client_insights,
    "whatif": whatif,
    "portfolio_projection": portfolio_projection,
    "book_analysis": book_analysis,
    "book_stress": book_stress,
}


def run_job(job_type: str, payload: dict) -> dict:
    if job_type not in JOBS:
        raise ValueError(f"unknown sim_kernel job type: {job_type!r}")
    return JOBS[job_type](payload)
