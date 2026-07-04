"""The market model — the assumptions layer the Monte Carlo engine runs on.

Two sources, chosen by the GPU switch (see `resolve_market_model`):

* **Derived** (GPU nightly): `mu`, `sigma`, `Sigma` computed from each category's real
  NAV history — the "our numbers come from data, not vibes" story. Trivial arithmetic
  (`diff(log(nav))` -> mean/std/cov), but we only take this path on the GPU box.
* **Fallback** (CPU / thin data): a hardcoded India-market table plus a factor-model
  correlation matrix. Sensible and always PSD, so the demo runs anywhere.

Everything is per **category** (the 14 canonical tags), never per fund. `Sigma` is 14x14
regardless of fund count; it is Cholesky-factored once to `L` and reused across every sim.

Pure NumPy on purpose: this runs once per night, off the hot path. The engine
(`montecarlo.py`) moves `mu`/`L` onto the GPU via `xp.asarray` at simulate time.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from .categories import CAT_INDEX, CATEGORIES, N_CATEGORIES

MIN_MONTHS = 24  # need at least this much history (per category and in the common window)


# ── Fallback assumptions (annualized), sensible India-market numbers ──────────
# mu = expected annual return, sigma = annual volatility.
FALLBACK_MU = {
    "high_risk_equity": 0.15, "mid_risk_equity": 0.13, "low_risk_equity": 0.11,
    "international_equity": 0.11, "cash_equivalent": 0.05, "good_debt": 0.07,
    "bad_debt": 0.08, "gold": 0.08, "silver": 0.08, "aggressive_hybrid": 0.12,
    "balanced_advantage": 0.10, "conservative_hybrid": 0.08, "multi_asset": 0.10,
    "other": 0.08,
}
FALLBACK_SIGMA = {
    "high_risk_equity": 0.28, "mid_risk_equity": 0.22, "low_risk_equity": 0.16,
    "international_equity": 0.18, "cash_equivalent": 0.01, "good_debt": 0.04,
    "bad_debt": 0.09, "gold": 0.15, "silver": 0.27, "aggressive_hybrid": 0.15,
    "balanced_advantage": 0.10, "conservative_hybrid": 0.07, "multi_asset": 0.11,
    "other": 0.12,
}

# Factor loadings drive the fallback correlation matrix. Each category is expressed as a
# mix of latent factors [equity, intl, duration, credit, gold, silver]; correlation is
# then B @ B.T with idiosyncratic variance filling the diagonal to 1. Building it this
# way guarantees a valid (positive semi-definite) matrix. Gold/silver load slightly
# NEGATIVE on equity so the classic equity-gold hedge shows up.
_FACTORS = ["equity", "intl", "duration", "credit", "gold", "silver"]
_LOADINGS = {
    "high_risk_equity":    [0.92,  0.05, 0.00, 0.00, 0.00, 0.00],
    "mid_risk_equity":     [0.90,  0.05, 0.00, 0.00, 0.00, 0.00],
    "low_risk_equity":     [0.85,  0.05, 0.00, 0.00, 0.00, 0.00],
    "international_equity": [0.35,  0.80, 0.00, 0.00, 0.00, 0.00],
    "cash_equivalent":     [0.00,  0.00, 0.15, 0.00, 0.00, 0.00],
    "good_debt":           [0.00,  0.00, 0.78, 0.10, 0.00, 0.00],
    "bad_debt":            [0.05,  0.00, 0.35, 0.75, 0.00, 0.00],
    "gold":                [-0.12, 0.00, 0.05, 0.00, 0.90, 0.00],
    "silver":              [-0.08, 0.00, 0.00, 0.00, 0.45, 0.80],
    "aggressive_hybrid":   [0.75,  0.03, 0.30, 0.05, 0.05, 0.00],
    "balanced_advantage":  [0.55,  0.02, 0.45, 0.05, 0.05, 0.00],
    "conservative_hybrid": [0.35,  0.02, 0.60, 0.05, 0.05, 0.00],
    "multi_asset":         [0.50,  0.03, 0.30, 0.03, 0.35, 0.05],
    "other":               [0.40,  0.02, 0.30, 0.05, 0.05, 0.00],
}


@dataclass(frozen=True)
class MarketModel:
    mu: np.ndarray          # (14,) annual expected return per category
    sigma: np.ndarray       # (14,) annual volatility per category
    Sigma: np.ndarray       # (14,14) annualized covariance
    L: np.ndarray           # (14,14) Cholesky factor of Sigma
    source: str             # "derived" | "fallback"
    n_months: int = 0       # months of history behind a derived model (0 for fallback)


def _fallback_correlation() -> np.ndarray:
    B = np.array([_LOADINGS[c] for c in CATEGORIES], dtype=float)  # (14, 6)
    C = B @ B.T
    idio = np.clip(1.0 - np.diag(C), 0.0, None)  # fill diagonal to exactly 1
    corr = C + np.diag(idio)
    return corr


def fallback_assumptions() -> MarketModel:
    """The hardcoded model — always valid, used on CPU / when derivation can't be trusted."""
    mu = np.array([FALLBACK_MU[c] for c in CATEGORIES], dtype=float)
    sigma = np.array([FALLBACK_SIGMA[c] for c in CATEGORIES], dtype=float)
    Sigma = _fallback_correlation() * np.outer(sigma, sigma)
    Sigma, L = cholesky_psd(Sigma)
    return MarketModel(mu=mu, sigma=sigma, Sigma=Sigma, L=L, source="fallback")


def cholesky_psd(Sigma: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Cholesky-factor Sigma, repairing it to positive-definite if needed.

    A sample covariance from few months can be rank-deficient or slightly non-PSD,
    which makes plain Cholesky throw. We symmetrize, clip eigenvalues to a small floor,
    and rebuild — the smallest change that guarantees a usable factor. Returns the
    (possibly repaired) Sigma alongside its lower-triangular L.
    """
    Sigma = (Sigma + Sigma.T) / 2  # kill floating-point asymmetry
    try:
        return Sigma, np.linalg.cholesky(Sigma)
    except np.linalg.LinAlgError:
        w, V = np.linalg.eigh(Sigma)
        w = np.clip(w, 1e-10, None)
        repaired = (V * w) @ V.T
        repaired = (repaired + repaired.T) / 2
        return repaired, np.linalg.cholesky(repaired)


def _category_monthly_returns(session: Session) -> dict[str, dict]:
    """{category: {year_month: equal-weighted mean log-return of its funds that month}}.

    Month-end NAV per fund is taken in SQL (last NAV in each calendar month); we then
    diff the logs in Python and equal-weight funds within a category, month by month.
    """
    rows = session.execute(text("""
        select f.category, m.fund_id, m.ym, m.nav
        from (
            select fund_id,
                   date_trunc('month', date) as ym,
                   (array_agg(nav order by date desc))[1] as nav
            from nav_history
            group by fund_id, date_trunc('month', date)
        ) m
        join funds f on f.id = m.fund_id
        order by f.category, m.fund_id, m.ym
    """)).all()

    # Group month-end NAVs per fund, preserving chronological order.
    per_fund: dict[int, list[tuple]] = {}
    fund_cat: dict[int, str] = {}
    for category, fund_id, ym, nav in rows:
        fund_cat[fund_id] = category
        per_fund.setdefault(fund_id, []).append((ym, float(nav)))

    # Per fund -> {month: log-return vs previous month}. Accumulate into category buckets.
    cat_month_sum: dict[str, dict] = {c: {} for c in CATEGORIES}
    cat_month_cnt: dict[str, dict] = {c: {} for c in CATEGORIES}
    for fund_id, series in per_fund.items():
        cat = fund_cat[fund_id]
        for (_, prev_nav), (ym, nav) in zip(series, series[1:]):
            if prev_nav > 0 and nav > 0:
                r = float(np.log(nav / prev_nav))
                cat_month_sum[cat][ym] = cat_month_sum[cat].get(ym, 0.0) + r
                cat_month_cnt[cat][ym] = cat_month_cnt[cat].get(ym, 0) + 1

    return {
        c: {ym: cat_month_sum[c][ym] / cat_month_cnt[c][ym] for ym in cat_month_sum[c]}
        for c in CATEGORIES
    }


def derive_assumptions(session: Session) -> Optional[MarketModel]:
    """Derive (mu, sigma, Sigma, L) from nav_history. Returns None if data is too thin
    to trust — the caller then falls back. mu/sigma use each category's full history;
    Sigma uses the window common to all categories so the matrix is properly aligned.
    """
    cat_returns = _category_monthly_returns(session)

    # Every category needs a minimum of history, else the matrix is unreliable.
    if any(len(cat_returns[c]) < MIN_MONTHS for c in CATEGORIES):
        thin = [c for c in CATEGORIES if len(cat_returns[c]) < MIN_MONTHS]
        print(f"[market] thin history for {thin}; using fallback.")
        return None

    mu = np.array([np.mean(list(cat_returns[c].values())) * 12 for c in CATEGORIES])

    common = set.intersection(*(set(cat_returns[c]) for c in CATEGORIES))
    if len(common) < MIN_MONTHS:
        print(f"[market] common return window {len(common)}m < {MIN_MONTHS}m; using fallback.")
        return None

    months = sorted(common)
    R = np.array([[cat_returns[c][m] for m in months] for c in CATEGORIES])  # (14, T)
    Sigma = np.cov(R) * 12
    sigma = np.sqrt(np.diag(Sigma))
    Sigma, L = cholesky_psd(Sigma)
    return MarketModel(
        mu=mu, sigma=sigma, Sigma=Sigma, L=L, source="derived", n_months=len(months)
    )


def resolve_market_model(session: Session) -> MarketModel:
    """The single entry point. Derive from NAV history when a GPU is declared available;
    otherwise use the fallback table. Any derivation failure degrades to fallback."""
    from ..config import settings

    if settings.is_gpu_available:
        model = derive_assumptions(session)
        if model is not None:
            print(f"[market] derived from {model.n_months} months of NAV history.")
            return model
    return fallback_assumptions()


# ── Persistence (assumptions + covariances tables) ───────────────────────────
def persist(session: Session, model: MarketModel) -> None:
    """Upsert the model into `assumptions` (14 rows) and `covariances` (14x14 pairs)
    so APIs and the client-facing story can read the numbers the book was scored on."""
    for c in CATEGORIES:
        i = CAT_INDEX[c]
        session.execute(
            text("""
                insert into assumptions (category, mu, sigma) values (:c, :mu, :sig)
                on conflict (category) do update set mu = excluded.mu, sigma = excluded.sigma
            """),
            {"c": c, "mu": float(model.mu[i]), "sig": float(model.sigma[i])},
        )
    for a in CATEGORIES:
        for b in CATEGORIES:
            session.execute(
                text("""
                    insert into covariances (cat_a, cat_b, cov) values (:a, :b, :cov)
                    on conflict (cat_a, cat_b) do update set cov = excluded.cov
                """),
                {"a": a, "b": b, "cov": float(model.Sigma[CAT_INDEX[a], CAT_INDEX[b]])},
            )


def load_persisted(session: Session) -> Optional[MarketModel]:
    """Rebuild a MarketModel from the DB. Returns None if not yet persisted."""
    arows = session.execute(text("select category, mu, sigma from assumptions")).all()
    if len(arows) < N_CATEGORIES:
        return None
    mu = np.zeros(N_CATEGORIES)
    sigma = np.zeros(N_CATEGORIES)
    for cat, m, s in arows:
        mu[CAT_INDEX[cat]] = float(m)
        sigma[CAT_INDEX[cat]] = float(s)

    Sigma = np.zeros((N_CATEGORIES, N_CATEGORIES))
    for a, b, cov in session.execute(text("select cat_a, cat_b, cov from covariances")):
        Sigma[CAT_INDEX[a], CAT_INDEX[b]] = float(cov)
    Sigma, L = cholesky_psd(Sigma)
    return MarketModel(mu=mu, sigma=sigma, Sigma=Sigma, L=L, source="persisted")
