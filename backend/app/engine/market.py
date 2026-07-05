"""The market model — the assumptions layer the Monte Carlo engine runs on.

Two sources, chosen by the GPU switch (see `resolve_market_model`):

* **Derived** (GPU nightly): `mu`, `sigma`, `Sigma` computed from each category's real
  NAV history — the "our numbers come from data, not vibes" story. Trivial arithmetic
  (`diff(log(nav))` -> mean/std/cov), but we only take this path when GPU compute is
  configured.
* **Fallback** (CPU / thin data): a hardcoded India-market table plus a factor-model
  correlation matrix. Sensible and always PSD, so the demo runs anywhere.

Everything is per **category** (the 14 canonical tags), never per fund. `Sigma` is 14x14
regardless of fund count; it is Cholesky-factored once to `L` and reused across every sim.

Pure NumPy on purpose: this runs once per night, off the hot path — no reason to ship it
to the GPU worker. `MarketModel`, `fallback_assumptions()`, and `cholesky_psd()` live in
`sim_kernel.state` (shared with the worker); this module only adds the DB-bound half
(deriving from `nav_history`, persisting, reloading).
"""

from typing import Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from sim_kernel.categories import CAT_INDEX, CATEGORIES, N_CATEGORIES
from sim_kernel.state import MarketModel, cholesky_psd, fallback_assumptions

__all__ = ["MarketModel", "fallback_assumptions", "cholesky_psd",
           "derive_assumptions", "resolve_market_model", "persist", "load_persisted"]

MIN_MONTHS = 24  # need at least this much history (per category and in the common window)


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
    """The single entry point. Derive from NAV history when GPU compute is configured;
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
