"""The wire-format dataclasses — what a job payload actually carries.

`MarketModel` (mu/sigma/Σ/L) and `ClientState`/`GoalState` (holdings/SIP/goals, all in the
14-category vector) are pure data: plain numpy arrays + primitives, never a DB handle or
an open session. That's what makes them safe to serialize into a RunPod job body and
rebuild on the other side with `from_payload`.

`fallback_assumptions()`/`cholesky_psd()` live here too — they're the pure-numpy half of
the market model (the DB-driven half, `derive_assumptions()`, stays in the backend app's
`engine/market.py` since it needs a live `Session`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from .categories import CATEGORIES, N_CATEGORIES

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
    source: str             # "derived" | "fallback" | "persisted"
    n_months: int = 0       # months of history behind a derived model (0 for fallback)

    def to_payload(self) -> dict:
        """Only `mu`/`L` are what the engine actually consumes at simulate time."""
        return {"mu": self.mu.tolist(), "L": self.L.tolist()}

    @staticmethod
    def from_payload(d: dict) -> "MarketModel":
        mu = np.asarray(d["mu"], dtype=float)
        L = np.asarray(d["L"], dtype=float)
        n = mu.shape[0]
        return MarketModel(
            mu=mu, sigma=np.zeros(n), Sigma=np.zeros((n, n)), L=L, source="payload",
        )


def _fallback_correlation() -> np.ndarray:
    B = np.array([_LOADINGS[c] for c in CATEGORIES], dtype=float)  # (14, 6)
    C = B @ B.T
    idio = np.clip(1.0 - np.diag(C), 0.0, None)  # fill diagonal to exactly 1
    corr = C + np.diag(idio)
    return corr


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


def fallback_assumptions() -> MarketModel:
    """The hardcoded model — always valid, used when derivation can't be trusted."""
    mu = np.array([FALLBACK_MU[c] for c in CATEGORIES], dtype=float)
    sigma = np.array([FALLBACK_SIGMA[c] for c in CATEGORIES], dtype=float)
    Sigma = _fallback_correlation() * np.outer(sigma, sigma)
    Sigma, L = cholesky_psd(Sigma)
    return MarketModel(mu=mu, sigma=sigma, Sigma=Sigma, L=L, source="fallback")


@dataclass
class GoalState:
    goal_id: int
    name: str | None
    target_amount: float
    horizon_months: int
    holdings: np.ndarray                  # (14,) ₹ per category, this goal's funds only
    monthly_sip: np.ndarray               # (14,)
    stepup_rate: float = 0.0

    @property
    def start_value(self) -> float:
        return float(self.holdings.sum())

    def to_payload(self) -> dict:
        return {
            "goal_id": self.goal_id, "name": self.name,
            "target_amount": self.target_amount, "horizon_months": self.horizon_months,
            "holdings": self.holdings.tolist(), "monthly_sip": self.monthly_sip.tolist(),
            "stepup_rate": self.stepup_rate,
        }

    @staticmethod
    def from_payload(d: dict) -> "GoalState":
        return GoalState(
            goal_id=d["goal_id"], name=d.get("name"),
            target_amount=float(d["target_amount"]), horizon_months=int(d["horizon_months"]),
            holdings=np.asarray(d["holdings"], dtype=float),
            monthly_sip=np.asarray(d["monthly_sip"], dtype=float),
            stepup_rate=float(d.get("stepup_rate") or 0.0),
        )


@dataclass
class ClientState:
    id: int
    name: str
    risk_profile: str
    holdings: np.ndarray                  # (14,) ₹ per category (whole portfolio)
    monthly_sip: np.ndarray               # (14,)
    stepup_rate: float
    fund_value: dict = field(default_factory=dict)     # {fund_id: ₹}  (for concentration)
    category_value: dict = field(default_factory=dict)  # {category_tag: ₹}  (nonzero only)
    goals: list[GoalState] = field(default_factory=list)
    mu: np.ndarray | None = None         # attached from the market model
    L: np.ndarray | None = None

    @property
    def total(self) -> float:
        return float(self.holdings.sum())

    def with_market(self, model: MarketModel) -> "ClientState":
        self.mu, self.L = model.mu, model.L
        return self

    def to_payload(self, *, include_goals: bool = True) -> dict:
        """`fund_value`/`category_value` never leave the backend — concentration flags are
        pure arithmetic, computed server-side, no reason to ship them to the worker."""
        d = {
            "id": self.id, "name": self.name, "risk_profile": self.risk_profile,
            "holdings": self.holdings.tolist(), "monthly_sip": self.monthly_sip.tolist(),
            "stepup_rate": self.stepup_rate,
        }
        if include_goals:
            d["goals"] = [g.to_payload() for g in self.goals]
        return d

    @staticmethod
    def from_payload(d: dict) -> "ClientState":
        return ClientState(
            id=d["id"], name=d.get("name", ""), risk_profile=d.get("risk_profile") or "balanced",
            holdings=np.asarray(d["holdings"], dtype=float),
            monthly_sip=np.asarray(d["monthly_sip"], dtype=float),
            stepup_rate=float(d.get("stepup_rate") or 0.0),
            goals=[GoalState.from_payload(g) for g in d.get("goals", [])],
        )
