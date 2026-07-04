"""The fourteen category tags — the canonical "asset classes" the engine simulates.

Pinned order. Reuse CAT_INDEX everywhere a category vector or the rows/cols of the
14x14 covariance matrix are indexed. Never reorder without regenerating everything.
"""

CATEGORIES = [
    "high_risk_equity", "mid_risk_equity", "low_risk_equity", "international_equity",
    "cash_equivalent", "good_debt", "bad_debt", "gold",
    "silver", "aggressive_hybrid", "balanced_advantage", "conservative_hybrid",
    "multi_asset", "other",
]
CAT_INDEX = {c: i for i, c in enumerate(CATEGORIES)}
N_CATEGORIES = len(CATEGORIES)  # 14
