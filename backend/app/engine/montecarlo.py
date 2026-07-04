"""The one Monte Carlo engine — written once, reused for everything.

Pure function of its inputs: same inputs + same seed -> identical terminal values, on
CPU or GPU. It only ever sees **14-category vectors** (`montecarlo` never knows funds
exist; the loader rolls funds up first). The loop is over *months*, never over paths —
that is what keeps it embarrassingly parallel and GPU-friendly.

Every array op goes through `xp` (see `backend.py`), so the CuPy swap needs zero changes
here. Host inputs are moved onto the device once, up front, via `xp.asarray`.
"""

from ..config import settings
from .backend import GPU, asnumpy, timer, xp


def simulate(
    holdings,               # (14,)  current ₹ per category (rolled up from funds)
    mu,                     # (14,)  annual expected return per category
    L,                      # (14,14) Cholesky factor of the category covariance Σ
    monthly_sip,            # (14,)  contribution per category per month
    horizon_months: int,
    n_paths: int | None = None,
    steps_per_year: int | None = None,
    seed: int | None = None,
    stepup_rate: float = 0.0,      # optional annual SIP step-up
    shock: dict | None = None,     # e.g. {"month": 0, "deltas": {cat_idx: -0.20}}
):
    """Return terminal portfolio value per path — shape (n_paths,), on host memory.

    `shock` is how both single-client what-if and book-wide stress inject a market move:
    `shock["deltas"]` maps a category index (CAT_INDEX[...]) to a one-off multiplicative
    return applied at `shock["month"]`.
    """
    n_paths = n_paths or settings.mc_n_paths
    steps_per_year = steps_per_year or settings.mc_steps_per_year
    seed = settings.mc_seed if seed is None else seed

    mu = xp.asarray(mu, dtype=xp.float64)
    L = xp.asarray(L, dtype=xp.float64)
    holdings = xp.asarray(holdings, dtype=xp.float64)
    sip = xp.asarray(monthly_sip, dtype=xp.float64).copy()

    rng = xp.random.default_rng(seed)
    dt = 1.0 / steps_per_year
    n = holdings.shape[0]  # 14

    # Per-step lognormal drift. Variance term uses the category variances (diag of Σ = L Lᵀ).
    drift = (mu - 0.5 * xp.sum(L * L, axis=1)) * dt
    sqrt_dt = xp.sqrt(xp.asarray(dt))
    value = xp.tile(holdings, (n_paths, 1))  # (paths, 14)

    for t in range(horizon_months):
        z = rng.standard_normal((n_paths, n))
        correlated = (z @ L.T) * sqrt_dt          # correlated shocks across categories
        value *= xp.exp(drift + correlated)       # lognormal step
        if shock and shock.get("month") == t:     # inject a market shock
            for cat_idx, delta in shock["deltas"].items():
                value[:, cat_idx] *= (1 + delta)
        value += sip                              # inject SIP each month
        if stepup_rate and (t + 1) % steps_per_year == 0:
            sip = sip * (1 + stepup_rate)         # annual step-up

    return asnumpy(value.sum(axis=1))             # (paths,) terminal totals


def simulate_timed(*args, **kwargs):
    """simulate() plus wall-time + backend label, for the GPU-vs-CPU pitch number."""
    with timer() as elapsed:
        terminals = simulate(*args, **kwargs)
    return terminals, {"seconds": elapsed(), "gpu": GPU}
