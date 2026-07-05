"""Live single-client what-if — hero demo #1.

Takes a client's current 14-category state (from the loader) plus a set of *levers*
the advisor pulls, and re-runs the same Monte Carlo engine to produce a before/after
diff: per-goal success probability + median terminal, and 1-year portfolio downside.

Everything is expressed as transforms on the 14-vectors the engine already consumes —
the engine never learns what a lever is. Same inputs + seed -> identical numbers, so a
what-if is reproducible and re-runs in ~1s (the GPU-vs-CPU pitch number rides along).

The levers (all optional, matching the `run_whatif` tool schema in IMPLEMENTATION.md §7):

* ``sip_delta``            — ₹ change to total monthly SIP, spread across categories by
                             the current contribution mix (or holdings mix if no SIP yet).
* ``lump_sum``             — one-time ₹ injection (+) / withdrawal (−), spread by holdings mix.
* ``reallocate``           — ``{from, to, pct}``: shift ``pct`` of the portfolio's weight
                             from one category to another (pct as a fraction, e.g. 0.10).
* ``reduce_concentration`` — ``{category, cap, to}``: cap a category's weight at ``cap`` and
                             move the excess into ``to`` (default ``good_debt``).
* ``horizon_shift``        — ± months applied to every goal's target date.
* ``return_shock``         — ``{category: delta}``: shift a category's expected annual return
                             (stress the assumptions, e.g. ``{"high_risk_equity": -0.02}``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import pipelines
from .categories import CAT_INDEX, N_CATEGORIES
from .state import ClientState, GoalState, MarketModel
from .montecarlo import simulate

RISK_HORIZON_MONTHS = 12  # matches insights/baseline: portfolio downside is measured over a year
DEFAULT_DERISK_TO = "good_debt"  # where reduce_concentration parks the trimmed weight


@dataclass
class Levers:
    """The what-if change set. All fields optional; an empty set is a no-op re-run."""

    sip_delta: float = 0.0
    lump_sum: float = 0.0
    reallocate: dict | None = None
    reduce_concentration: dict | None = None
    horizon_shift: int = 0
    return_shock: dict | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.sip_delta or self.lump_sum or self.reallocate
            or self.reduce_concentration or self.horizon_shift or self.return_shock
        )

    def describe(self) -> list[str]:
        """Human-readable one-liners for the trace / narration."""
        out: list[str] = []
        if self.sip_delta:
            sign = "+" if self.sip_delta > 0 else "−"
            out.append(f"{sign}₹{abs(self.sip_delta):,.0f}/mo SIP")
        if self.lump_sum:
            verb = "inject" if self.lump_sum > 0 else "withdraw"
            out.append(f"{verb} ₹{abs(self.lump_sum):,.0f} lump sum")
        if self.reallocate:
            r = self.reallocate
            out.append(
                f"move {_as_fraction(r.get('pct', 0)) * 100:.0f}% "
                f"{r.get('from')} → {r.get('to')}"
            )
        if self.reduce_concentration:
            rc = self.reduce_concentration
            out.append(
                f"cap {rc.get('category')} at {_as_fraction(rc.get('cap', 0)) * 100:.0f}%"
            )
        if self.horizon_shift:
            sign = "+" if self.horizon_shift > 0 else "−"
            out.append(f"{sign}{abs(self.horizon_shift)} mo horizon")
        if self.return_shock:
            out.append(
                "return shock " + ", ".join(
                    f"{c} {d * 100:+.0f}%" for c, d in self.return_shock.items()
                )
            )
        return out

    def to_payload(self) -> dict:
        return {
            "sip_delta": self.sip_delta, "lump_sum": self.lump_sum,
            "reallocate": self.reallocate, "reduce_concentration": self.reduce_concentration,
            "horizon_shift": self.horizon_shift, "return_shock": self.return_shock,
        }

    @staticmethod
    def from_payload(d: dict) -> "Levers":
        return Levers(
            sip_delta=float(d.get("sip_delta") or 0), lump_sum=float(d.get("lump_sum") or 0),
            reallocate=d.get("reallocate"), reduce_concentration=d.get("reduce_concentration"),
            horizon_shift=int(d.get("horizon_shift") or 0), return_shock=d.get("return_shock"),
        )


def _as_fraction(x: float) -> float:
    """Accept either a fraction (0.10) or a percent (10) — normalise to a fraction."""
    x = float(x or 0)
    return x / 100.0 if abs(x) > 1.0 else x


def _weights(vec: np.ndarray) -> np.ndarray:
    """Normalise a 14-vector to weights; uniform if it's all zeros."""
    total = float(vec.sum())
    if total > 0:
        return vec / total
    return np.full(N_CATEGORIES, 1.0 / N_CATEGORIES)


def _apply_allocation(vec: np.ndarray, levers: Levers) -> np.ndarray:
    """Apply the weight-shifting levers (reallocate, reduce_concentration) to one vector.

    Both are expressed relative to the vector's own total, so the same transform applies
    cleanly to the whole portfolio and to each goal's sub-portfolio.
    """
    v = vec.copy()
    total = float(v.sum())
    if total <= 0:
        return v

    if levers.reallocate:
        r = levers.reallocate
        src, dst = r.get("from"), r.get("to")
        pct = _as_fraction(r.get("pct", 0))
        if src in CAT_INDEX and dst in CAT_INDEX and pct > 0:
            i, j = CAT_INDEX[src], CAT_INDEX[dst]
            move = min(total * pct, v[i])  # can't move more than the source holds
            v[i] -= move
            v[j] += move

    if levers.reduce_concentration:
        rc = levers.reduce_concentration
        cat = rc.get("category")
        cap = _as_fraction(rc.get("cap", 0.25))
        dst = rc.get("to", DEFAULT_DERISK_TO)
        if cat in CAT_INDEX and dst in CAT_INDEX and cap > 0:
            i, j = CAT_INDEX[cat], CAT_INDEX[dst]
            ceiling = total * cap
            if v[i] > ceiling:
                excess = v[i] - ceiling
                v[i] -= excess
                v[j] += excess

    return v


def _transform(
    holdings: np.ndarray, sip: np.ndarray, levers: Levers, share: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return (holdings, sip) after applying every lever to one (sub-)portfolio.

    ``share`` (0..1) is this sub-portfolio's fraction of total holdings — it splits the
    absolute-₹ levers (lump_sum, sip_delta) across goals proportional to their size, so
    the goal diffs sum up to the whole-portfolio diff.
    """
    h = _apply_allocation(holdings, levers)
    s = sip.copy()

    if levers.lump_sum:
        h = np.clip(h + levers.lump_sum * share * _weights(h), 0.0, None)

    if levers.sip_delta:
        # New SIP money follows the existing contribution mix, else the holdings mix.
        mix = _weights(s) if s.sum() > 0 else _weights(h)
        s = np.clip(s + levers.sip_delta * share * mix, 0.0, None)

    return h, s


def _shocked_mu(mu: np.ndarray, levers: Levers) -> np.ndarray:
    """Apply return_shock as an additive shift to per-category expected annual return."""
    if not levers.return_shock:
        return mu
    out = mu.copy()
    for cat, delta in levers.return_shock.items():
        if cat in CAT_INDEX:
            out[CAT_INDEX[cat]] += float(delta)
    return out


def _goal_row(g: GoalState, mu, L, holdings, sip, horizon, n_paths, seed) -> dict:
    terminals = simulate(
        holdings, mu, L, sip, max(horizon, 1),
        n_paths=n_paths, seed=seed, stepup_rate=g.stepup_rate,
    )
    return {
        "success_prob": round(pipelines.goal_probability(terminals, g.target_amount), 4),
        "p50": round(pipelines.percentiles(terminals)["p50"]),
        "monthly_sip": round(float(sip.sum())),
    }


def run_whatif(
    state: ClientState, model: MarketModel, levers: Levers,
    n_paths: int, seed: int,
) -> dict:
    """Simulate the client twice — baseline and levered — and return the before/after diff.

    Per goal: success probability + median terminal + monthly SIP, before and after.
    Portfolio: 1-year VaR/CVaR/worst-case drawdown + suitability, before and after.
    """
    mu_before, L = model.mu, model.L
    mu_after = _shocked_mu(mu_before, levers)
    total = state.total
    tolerable = pipelines.TOLERABLE_DD.get(
        state.risk_profile, pipelines.TOLERABLE_DD["balanced"]
    )

    goals_out: list[dict] = []
    for g in state.goals:
        if g.target_amount <= 0:
            continue
        share = (float(g.holdings.sum()) / total) if total > 0 else 0.0
        h_after, s_after = _transform(g.holdings, g.monthly_sip, levers, share)
        horizon_after = g.horizon_months + levers.horizon_shift

        before = _goal_row(g, mu_before, L, g.holdings, g.monthly_sip, g.horizon_months, n_paths, seed)
        after = _goal_row(g, mu_after, L, h_after, s_after, horizon_after, n_paths, seed)
        goals_out.append({
            "goal_id": g.goal_id,
            "name": g.name,
            "target_amount": round(g.target_amount),
            "horizon_months": g.horizon_months,
            "horizon_months_after": max(horizon_after, 1),
            "before": before,
            "after": after,
            "prob_delta": round(after["success_prob"] - before["success_prob"], 4),
        })

    # Portfolio downside (current holdings, no new SIP) — before vs after the levers.
    def _risk(holdings, mu):
        if total <= 0:
            return {"var_95": 0.0, "cvar_95": 0.0, "max_drawdown": 0.0,
                    "suitability_mismatch": 0.0, "over_exposed": False, "start_value": 0.0}
        terminals = simulate(
            holdings, mu, L, np.zeros(N_CATEGORIES), RISK_HORIZON_MONTHS,
            n_paths=n_paths, seed=seed,
        )
        var, cvar = pipelines.var_cvar(terminals, float(holdings.sum()))
        dd = pipelines.simulated_drawdown(terminals, float(holdings.sum()))
        mismatch = pipelines.suitability_mismatch(dd, state.risk_profile)
        return {
            "var_95": round(var, 4), "cvar_95": round(cvar, 4),
            "max_drawdown": round(dd, 4),
            "suitability_mismatch": round(mismatch, 4),
            "over_exposed": mismatch > 0,
            "start_value": round(float(holdings.sum())),
        }

    h_after, _ = _transform(state.holdings, state.monthly_sip, levers, 1.0)
    portfolio = {
        "tolerable_dd": tolerable,
        "before": _risk(state.holdings, mu_before),
        "after": _risk(h_after, mu_after),
    }

    return {
        "client_id": state.id,
        "client_name": state.name,
        "risk_profile": state.risk_profile,
        "levers": levers.describe(),
        "goals": goals_out,
        "portfolio": portfolio,
    }
