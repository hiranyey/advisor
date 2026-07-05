"""The one Monte Carlo engine — written once, reused for everything.

Pure function of its inputs: same inputs + same seed -> identical terminal values, on
CPU or GPU. It only ever sees **14-category vectors** (`montecarlo` never knows funds
exist; the loader rolls funds up first). The loop is over *months*, never over paths —
that is what keeps it embarrassingly parallel and GPU-friendly.

Every array op goes through `xp` (see `backend.py`), so the CuPy swap needs zero changes
here. Host inputs are moved onto the device once, up front, via `xp.asarray`.

Every caller now passes `n_paths`/`steps_per_year`/`seed` explicitly (the RunPod job
payload always sets them) — the defaults below only cover ad-hoc/local use.
"""

from .backend import GPU, asnumpy, timer, xp

DEFAULT_N_PATHS = 8000
DEFAULT_STEPS_PER_YEAR = 12
DEFAULT_SEED = 42


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
    n_paths = n_paths or DEFAULT_N_PATHS
    steps_per_year = steps_per_year or DEFAULT_STEPS_PER_YEAR
    seed = DEFAULT_SEED if seed is None else seed

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


def simulate_batch(
    holdings,               # (batch, 14)  one row per client/goal
    mu,                     # (14,)  shared market model — same for every item in the batch
    L,                      # (14,14) shared market model
    monthly_sip,            # (batch, 14)
    horizon_months: int,    # shared — every item must step forward the same number of months
    n_paths: int | None = None,
    steps_per_year: int | None = None,
    seed: int | None = None,
    stepup_rate=0.0,        # scalar or (batch,)
    shock: dict | None = None,     # shared — same shock applied to every item in the batch
):
    """`simulate()`, batched across many clients/goals in one set of GPU kernel launches.

    Only `holdings`/`monthly_sip`/`stepup_rate` vary per item — `mu`/`L` (the market
    model) and `horizon_months`/`shock` are shared across the whole batch, which is why
    callers group items by horizon before batching (see `jobs.py`): items on different
    horizons can't step forward together. Returns terminals, shape (batch, n_paths).

    Runs in **float32**, unlike the single-item `simulate()` (float64). A `(batch,
    n_paths, 14)` block at float64 doubles memory for no real precision benefit here —
    ₹ terminal values and probability estimates don't need 15 significant digits — and
    at `batch=100`, `n_paths` in the hundreds of thousands, float64 was OOM-ing on the
    GPU worker. `jobs.py` also caps the batch size to whatever's actually free on the
    device, so this is a belt-and-suspenders fix, not a substitute for that check.
    """
    n_paths = n_paths or DEFAULT_N_PATHS
    steps_per_year = steps_per_year or DEFAULT_STEPS_PER_YEAR
    seed = DEFAULT_SEED if seed is None else seed

    mu = xp.asarray(mu, dtype=xp.float32)              # (14,)
    L = xp.asarray(L, dtype=xp.float32)                 # (14,14)
    holdings = xp.asarray(holdings, dtype=xp.float32)   # (batch, 14)
    sip = xp.asarray(monthly_sip, dtype=xp.float32).copy()  # (batch, 14)
    stepup_rate = xp.asarray(stepup_rate, dtype=xp.float32)
    batch, n = holdings.shape  # n == 14
    if stepup_rate.ndim == 0:
        stepup_rate = xp.full(batch, stepup_rate, dtype=xp.float32)
    stepup_rate = stepup_rate[:, None]  # (batch, 1) — broadcasts against sip's category axis

    rng = xp.random.default_rng(seed)
    dt = xp.float32(1.0 / steps_per_year)

    # Per-step lognormal drift. Variance term uses the category variances (diag of Σ = L Lᵀ).
    drift = (mu - xp.float32(0.5) * xp.sum(L * L, axis=1)) * dt  # (14,) — shared, broadcasts below
    sqrt_dt = xp.sqrt(dt)
    value = xp.broadcast_to(holdings[:, None, :], (batch, n_paths, n)).astype(xp.float32).copy()

    for t in range(horizon_months):
        z = rng.standard_normal((batch, n_paths, n), dtype=xp.float32)
        correlated = (z @ L.T) * sqrt_dt           # (batch, paths, 14); `L` broadcasts over batch
        value *= xp.exp(drift + correlated)        # lognormal step
        if shock and shock.get("month") == t:      # inject a market shock (same for every item)
            for cat_idx, delta in shock["deltas"].items():
                value[:, :, cat_idx] *= (1 + delta)
        value += sip[:, None, :]                    # inject SIP each month
        if (t + 1) % steps_per_year == 0:
            sip = sip * (1 + stepup_rate)          # annual step-up (no-op where stepup_rate==0)

    return asnumpy(value.sum(axis=2, dtype=xp.float64))  # (batch, paths) terminal totals, ₹-precision
