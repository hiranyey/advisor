"""Reading the answers off a simulated distribution.

`montecarlo.simulate()` returns one number per path (terminal portfolio value). Everything
an advisor actually asks — "will she hit the goal?", "how bad is the tail?", "who's
over-exposed?" — is a statistic over that distribution, never a single point estimate.
These helpers turn terminals into those statistics, plus the book-wide suitability,
concentration, and stress logic.

Sign convention for downside: losses/drawdowns are reported as **positive magnitudes**
(0.28 == a 28% loss). `suitability_mismatch > 0` means the client's simulated downside
exceeds what their risk profile tolerates, i.e. over-exposed.
"""

import numpy as np

from ..config import settings
from .categories import CAT_INDEX
from .montecarlo import simulate

# Worst peacetime drawdown a profile should stomach (positive magnitudes).
TOLERABLE_DD = {"conservative": 0.10, "balanced": 0.20, "aggressive": 0.35}


# ── Per-goal statistics ──────────────────────────────────────────────────────
def goal_probability(terminals, target) -> float:
    """Share of futures that reach the target by the horizon."""
    return float((terminals >= target).mean())


def percentiles(terminals) -> dict:
    """Worst-case / median / optimistic terminal value (P5, P50, P90)."""
    p5, p50, p90 = np.quantile(terminals, [0.05, 0.5, 0.90])
    return {"p5": float(p5), "p50": float(p50), "p90": float(p90)}


def shortfall(terminals, target) -> dict:
    """Expected and worst-case (P5) rupee gap to target, for off-track goals."""
    gap = np.maximum(target - terminals, 0)
    return {
        "expected": float(gap.mean()),
        "worst_p5": float(max(np.quantile(target - terminals, 0.95), 0.0)),
    }


def required_sip(
    holdings, mu, L, target, horizon_months, weights,
    confidence: float | None = None, hi: float | None = None, iters: int = 20, **sim_kw
) -> float:
    """Bisect the total monthly SIP needed to lift success probability to `confidence`.

    `weights` (14-vector, sums to 1) allocates the new SIP across categories — pass the
    goal's current holdings mix so extra money follows the existing strategy. Returns ₹
    of total monthly contribution; `hi` (auto if omitted) is the upper search bound.
    """
    confidence = settings.mc_confidence if confidence is None else confidence
    weights = np.asarray(weights, dtype=float)
    if hi is None:
        hi = target / max(horizon_months, 1) + 1e4  # enough to fund the goal from SIP alone

    # If even the ceiling can't reach confidence, say so with the ceiling.
    top = goal_probability(simulate(holdings, mu, L, hi * weights, horizon_months, **sim_kw), target)
    if top < confidence:
        return float(hi)

    lo = 0.0
    for _ in range(iters):
        mid = (lo + hi) / 2
        p = goal_probability(simulate(holdings, mu, L, mid * weights, horizon_months, **sim_kw), target)
        lo, hi = (mid, hi) if p < confidence else (lo, mid)
    return float(hi)


# ── Per-client risk statistics ───────────────────────────────────────────────
def var_cvar(terminals, start_value, pct: float | None = None) -> tuple[float, float]:
    """VaR and CVaR as positive loss fractions of the starting value.

    VaR = loss not exceeded in (1-pct) of futures; CVaR = mean loss in the worst `pct`.
    """
    pct = settings.mc_var_pct if pct is None else pct
    if start_value <= 0:
        return 0.0, 0.0
    losses = (start_value - terminals) / start_value  # >0 == a loss
    var = float(np.quantile(losses, 1 - pct))
    tail = losses[losses >= var]
    cvar = float(tail.mean()) if tail.size else var
    return var, cvar


def simulated_drawdown(terminals, start_value, pct: float | None = None) -> float:
    """The downside used for suitability: worst-case (P-tail) loss magnitude, clipped at 0."""
    var, _ = var_cvar(terminals, start_value, pct)
    return max(var, 0.0)


def suitability_mismatch(simulated_dd: float, risk_profile: str) -> float:
    """simulated downside − tolerable downside (both magnitudes). >0 == over-exposed."""
    return simulated_dd - TOLERABLE_DD.get(risk_profile, TOLERABLE_DD["balanced"])


def concentration_flags(fund_values: dict, category_values: dict, total: float) -> list[str]:
    """Single-fund (>25%) and single-category (>40%) over-exposure flags."""
    flags = []
    if total <= 0:
        return flags
    if fund_values and max(fund_values.values()) / total > 0.25:
        flags.append("concentrated_fund")
    if category_values and max(category_values.values()) / total > 0.40:
        flags.append("concentrated_category")
    return flags


# ── Book-wide stress ─────────────────────────────────────────────────────────
def stress_book(clients, shock: dict, deterministic: bool = True) -> list[dict]:
    """Apply one market shock across every client; return the ranked breach list.

    `shock`: {category_tag: delta, ..., 'horizon_months': int}. Deterministic mode is
    instant weight×shock arithmetic (the literal "small-cap drops 20%" question); the MC
    mode adds correlated spillover via Σ. Both return clients whose loss breaches their
    tolerance, worst first.

    Each `client` is a ClientState (see engine/loader.py): needs `id`, `risk_profile`,
    `total`, `category_value` {tag: ₹}, and (MC mode) `holdings`, `mu`, `L`.
    """
    horizon = int(shock.get("horizon_months", 0)) if not deterministic else 0
    deltas_by_tag = {k: v for k, v in shock.items() if k in CAT_INDEX}

    out = []
    for c in clients:
        if c.total <= 0:
            continue
        if deterministic:
            loss = sum(
                c.category_value.get(tag, 0.0) / c.total * -delta
                for tag, delta in deltas_by_tag.items()
            )  # positive == a loss (negative delta -> positive loss)
        else:
            deltas = {CAT_INDEX[tag]: d for tag, d in deltas_by_tag.items()}
            terminals = simulate(
                c.holdings, c.mu, c.L, np.zeros_like(c.holdings),
                horizon_months=max(horizon, 1), shock={"month": 0, "deltas": deltas},
            )
            loss = (c.total - float(np.quantile(terminals, 0.05))) / c.total

        tol = TOLERABLE_DD.get(c.risk_profile, TOLERABLE_DD["balanced"])
        if loss > tol:  # breach: simulated loss exceeds tolerance
            out.append({
                "client_id": c.id,
                "loss": round(loss, 4),
                "tolerable": tol,
                "severity": round(loss - tol, 4),
            })
    return sorted(out, key=lambda x: x["severity"], reverse=True)
