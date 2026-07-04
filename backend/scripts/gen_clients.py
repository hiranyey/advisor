"""Deterministic synthetic client pool — 150 advisors' clients with varied goals,
portfolios and SIPs, built from the REAL funds + nav_history already in the DB.

Design goals (a book that tells a story on stage):
  - Every fund/NAV comes from the seeded data — buys are priced at genuine historical
    NAVs, current value uses the genuine latest NAV, so the derived latest_holdings
    view reflects real market moves.
  - The stated `risk_profile` and the ACTUAL investing `style` are decoupled, so the
    pool contains all combinations:
      · aligned      — conservative→safe funds, aggressive→equity-heavy (the sane majority)
      · over-exposed — conservative/balanced clients piled into high-risk equity "for no
                       reason" (these light up the Book Risk Radar / stress test)
      · under-risked — aggressive clients parked in debt/cash (return drag)
      · concentrated — a single fund or category dominating the portfolio (>25% / >40%)
  - Fully deterministic: a fixed RNG seed + truncate-and-reload means re-running seed.py
    reproduces the exact same book. Generate once, reuse forever.

Portfolio construction works in CURRENT-VALUE space: we pick target category weights,
convert to units via each fund's latest NAV, then back-date the buy(s) at that date's
historical NAV. So concentration/allocation are exact as the holdings view will show them.
"""

import bisect
from collections import defaultdict
from datetime import date, timedelta
from random import Random

from sqlalchemy import text

from app.db import SessionLocal
from app.models import Client, Goal, GoalHolding, SipSchedule, Transaction

SEED = 42
N_CLIENTS = 150
BASE_DATE = date(2026, 7, 4)  # fixed "today" for reproducibility
PER_CATEGORY = 15             # funds sampled into the investable universe per category
MIN_HISTORY_DAYS = 500        # need ~2y of NAVs so back-dated buys can be priced

# ── Names ─────────────────────────────────────────────────────────────────────
FIRST = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rahul", "Rohan", "Kabir", "Ananya", "Diya", "Aadhya", "Saanvi",
    "Aanya", "Pari", "Anika", "Navya", "Meera", "Priya", "Neha", "Kavya",
    "Riya", "Ishita", "Nikhil", "Karan", "Varun", "Siddharth", "Ayaan", "Dev",
]
LAST = [
    "Sharma", "Verma", "Gupta", "Iyer", "Nair", "Reddy", "Rao", "Menon",
    "Patel", "Shah", "Mehta", "Desai", "Kulkarni", "Joshi", "Bose", "Chatterjee",
    "Banerjee", "Kapoor", "Malhotra", "Chopra", "Sinha", "Pillai", "Naidu", "Das",
]

# ── Investing styles: target category weights (normalized at pick time) ─────────
STYLE_ALLOCATIONS = {
    "safe": {
        "cash_equivalent": 0.25, "good_debt": 0.40, "conservative_hybrid": 0.20,
        "gold": 0.10, "low_risk_equity": 0.05,
    },
    "conservative_growth": {
        "good_debt": 0.30, "conservative_hybrid": 0.20, "balanced_advantage": 0.20,
        "low_risk_equity": 0.15, "gold": 0.10, "cash_equivalent": 0.05,
    },
    "balanced": {
        "balanced_advantage": 0.20, "aggressive_hybrid": 0.20, "low_risk_equity": 0.15,
        "mid_risk_equity": 0.15, "good_debt": 0.15, "gold": 0.10, "international_equity": 0.05,
    },
    "growth": {
        "mid_risk_equity": 0.30, "low_risk_equity": 0.15, "high_risk_equity": 0.15,
        "aggressive_hybrid": 0.15, "international_equity": 0.10, "gold": 0.10, "multi_asset": 0.05,
    },
    "aggressive": {
        "high_risk_equity": 0.40, "mid_risk_equity": 0.25, "international_equity": 0.15,
        "silver": 0.05, "multi_asset": 0.10, "low_risk_equity": 0.05,
    },
    "very_aggressive": {
        "high_risk_equity": 0.55, "mid_risk_equity": 0.25, "international_equity": 0.15,
        "silver": 0.05,
    },
}

# Per stated risk_profile, the mix of ACTUAL styles clients end up with.
# The tails (e.g. conservative→very_aggressive) are the deliberate mismatches.
STYLE_BY_PROFILE = {
    "conservative": [
        ("safe", 0.50), ("conservative_growth", 0.25), ("balanced", 0.12),
        ("growth", 0.08), ("aggressive", 0.04), ("very_aggressive", 0.01),
    ],
    "balanced": [
        ("conservative_growth", 0.15), ("balanced", 0.42), ("growth", 0.20),
        ("safe", 0.08), ("aggressive", 0.12), ("very_aggressive", 0.03),
    ],
    "aggressive": [
        ("aggressive", 0.38), ("very_aggressive", 0.25), ("growth", 0.20),
        ("balanced", 0.10), ("conservative_growth", 0.04), ("safe", 0.03),
    ],
}
PROFILE_WEIGHTS = [("conservative", 0.30), ("balanced", 0.45), ("aggressive", 0.25)]

# ── Goals: target-amount range (₹) and years-to-target range ────────────────────
GOAL_TYPES = {
    "Retirement":        ((1.0e7, 5.0e7), (15, 30)),
    "Child's Education": ((3.0e6, 1.2e7), (8, 18)),
    "Buy a House":       ((5.0e6, 2.0e7), (4, 10)),
    "Child's Wedding":   ((2.0e6, 6.0e6), (6, 15)),
    "New Car":           ((8.0e5, 2.5e6), (2, 5)),
    "Emergency Fund":    ((3.0e5, 1.5e6), (1, 3)),
    "World Tour":        ((1.0e6, 4.0e6), (2, 6)),
}


def _weighted(rng: Random, pairs):
    """Pick a key from [(key, weight), ...]."""
    r = rng.random() * sum(w for _, w in pairs)
    upto = 0.0
    for key, w in pairs:
        upto += w
        if r <= upto:
            return key
    return pairs[-1][0]


def build_universe(cur, rng: Random):
    """Sample a realistic investable universe per category and preload NAV series.

    Returns (universe, series):
      universe: {category: [(fund_id, name), ...]}
      series:   {fund_id: (dates[list[date]], navs[list[float]])}  sorted by date
    """
    cur.execute(
        "select fund_id, count(*), max(date) from nav_history group by fund_id"
    )
    stats = {fid: (cnt, mx) for fid, cnt, mx in cur.fetchall()}

    cur.execute("select id, name, category from funds")
    by_cat = defaultdict(list)
    for fid, name, category in cur.fetchall():
        st = stats.get(fid)
        if not st or st[0] < MIN_HISTORY_DAYS:
            continue
        by_cat[category].append((fid, name))

    universe: dict[str, list] = {}
    chosen_ids: list[int] = []
    for cat, funds in by_cat.items():
        funds = sorted(funds, key=lambda x: x[0])  # stable before shuffle
        rng.shuffle(funds)
        pick = funds[:PER_CATEGORY]
        universe[cat] = pick
        chosen_ids.extend(fid for fid, _ in pick)

    series: dict[int, tuple[list, list]] = {fid: ([], []) for fid in chosen_ids}
    cur.execute(
        "select fund_id, date, nav from nav_history "
        "where fund_id = any(%s) order by fund_id, date",
        (chosen_ids,),
    )
    for fid, d, nav in cur.fetchall():
        ds, ns = series[fid]
        ds.append(d)
        ns.append(float(nav))

    # Drop funds with an unusable series (empty or non-positive latest NAV) so
    # pricing never divides by zero.
    bad = {fid for fid, (ds, ns) in series.items() if not ns or ns[-1] <= 0}
    if bad:
        for cat in universe:
            universe[cat] = [(fid, name) for fid, name in universe[cat] if fid not in bad]
        for fid in bad:
            series.pop(fid, None)
    return universe, series


def _nav_on_or_before(series, fid, d):
    """(nav_date, nav) at or before d — clamps to the first available point."""
    ds, ns = series[fid]
    i = bisect.bisect_right(ds, d) - 1
    if i < 0:
        i = 0
    return ds[i], ns[i]


def _pick_weights(rng: Random, style, universe, concentrated: bool):
    """Choose funds and their CURRENT-VALUE weights for a style. -> [(fund_id, weight)].

    A heavy category (>18% target) is spread across two distinct funds so ordinary
    portfolios don't trip the single-fund concentration flag — that flag is reserved
    for the deliberately `concentrated` archetype below."""
    alloc = STYLE_ALLOCATIONS[style]
    picks = []
    for cat, w in alloc.items():
        pool = universe.get(cat)
        if not pool:
            continue
        if w > 0.18 and len(pool) >= 2:
            f1, f2 = rng.sample(pool, 2)
            split = rng.uniform(0.4, 0.6)
            picks.append([f1[0], w * split])
            picks.append([f2[0], w * (1 - split)])
        else:
            picks.append([rng.choice(pool)[0], w])
    total = sum(w for _, w in picks) or 1.0
    picks = [[fid, w / total] for fid, w in picks]

    if concentrated and picks:
        i = rng.randrange(len(picks))
        big = rng.uniform(0.55, 0.72)
        others = [j for j in range(len(picks)) if j != i]
        rest_src = sum(picks[j][1] for j in others) or 1.0
        for j in range(len(picks)):
            picks[j][1] = big if j == i else picks[j][1] / rest_src * (1 - big)
    return [(fid, w) for fid, w in picks]


def _make_buys(rng: Random, series, fid, target_value):
    """Back-dated buy tranches whose CURRENT value == target_value. -> [(date, units, nav, amount)]."""
    ds, ns = series[fid]
    latest_nav = ns[-1]
    total_units = target_value / latest_nav

    earliest = ds[0] + timedelta(days=30)
    horizon = BASE_DATE - timedelta(days=int(rng.uniform(365, 6 * 365)))
    lo = max(earliest, horizon)
    hi = BASE_DATE - timedelta(days=30)
    if lo >= hi:
        lo = hi - timedelta(days=30)

    n = rng.choice([1, 1, 2, 3])
    rows = []
    for _ in range(n):
        span = (hi - lo).days
        d = lo + timedelta(days=rng.randrange(span)) if span > 1 else lo
        nav_date, nav = _nav_on_or_before(series, fid, d)
        units = round(total_units / n, 4)
        rows.append((nav_date, units, round(nav, 4), round(units * nav, 2)))
    return rows


def _make_goals(rng: Random):
    """1–3 distinct goals. -> [(name, target_amount, target_date)]."""
    n = rng.choice([1, 1, 2, 2, 2, 3])
    names = rng.sample(list(GOAL_TYPES), min(n, len(GOAL_TYPES)))
    goals = []
    for name in names:
        (amt_lo, amt_hi), (yr_lo, yr_hi) = GOAL_TYPES[name]
        amount = round(rng.uniform(amt_lo, amt_hi), -4)  # nearest ₹10k
        years = rng.randint(yr_lo, yr_hi)
        target = BASE_DATE + timedelta(days=years * 365 + rng.randrange(365))
        goals.append((name, amount, target))
    return goals


def _client_plan(rng: Random, i: int, universe):
    """Decide one client's profile, style and edge-case flags."""
    # Force a visible block of extreme cases so the demo always has them.
    if i < 6:                       # severe suitability mismatch
        profile, style, concentrated = "conservative", "very_aggressive", False
    elif i < 12:                    # concentration flag
        profile = _weighted(rng, PROFILE_WEIGHTS)
        style, concentrated = "aggressive", True
    else:                           # the organic distribution
        profile = _weighted(rng, PROFILE_WEIGHTS)
        style = _weighted(rng, STYLE_BY_PROFILE[profile])
        concentrated = rng.random() < 0.08
    return profile, style, concentrated


def generate(session=None) -> dict:
    """Build the whole synthetic book. Truncates client-side tables first (idempotent)."""
    rng = Random(SEED)
    own_session = session is None
    session = session or SessionLocal()
    stats = defaultdict(int)

    try:
        # Wipe client-side tables (cascades clear goals/txns/sips/goal_holdings). Funds
        # and nav_history are untouched.
        session.execute(text("truncate clients restart identity cascade"))

        raw = session.connection().connection  # DBAPI connection for the universe query
        with raw.cursor() as cur:
            universe, series = build_universe(cur, rng)

        for i in range(N_CLIENTS):
            profile, style, concentrated = _client_plan(rng, i, universe)

            client = Client(
                name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                age=rng.randint(24, 68),
                risk_profile=profile,
            )
            session.add(client)

            goals_spec = _make_goals(rng)
            goal_objs = []
            for name, amount, target in goals_spec:
                g = Goal(client=client, name=name, target_amount=amount, target_date=target)
                session.add(g)
                goal_objs.append(g)

            weights = _pick_weights(rng, style, universe, concentrated)
            portfolio_value = 10 ** rng.uniform(5.3, 7.7)  # ~₹2L – ₹5Cr, log-uniform

            for idx, (fid, w) in enumerate(weights):
                target_value = portfolio_value * w
                for nav_date, units, nav, amount in _make_buys(rng, series, fid, target_value):
                    if units <= 0:
                        continue
                    session.add(Transaction(
                        client=client, fund_id=fid, date=nav_date, type="buy",
                        units=units, nav=nav, amount=amount,
                    ))
                    stats["transactions"] += 1

                # Tag this fund to one of the client's goals (round-robin).
                goal = goal_objs[idx % len(goal_objs)]
                session.add(GoalHolding(client=client, fund_id=fid, goal=goal))
                stats["goal_holdings"] += 1

                # Most funds also get a forward SIP.
                if rng.random() < 0.6:
                    session.add(SipSchedule(
                        client=client, fund_id=fid,
                        monthly_amount=round(rng.uniform(2000, 60000), -2),
                        stepup_rate=rng.choice([0, 0, 0, 0.05, 0.10]),
                        start_date=BASE_DATE - timedelta(days=rng.randrange(30, 3 * 365)),
                        active=rng.random() < 0.9,
                    ))
                    stats["sips"] += 1

            stats["clients"] += 1
            stats[f"style:{style}"] += 1
            stats[f"profile:{profile}"] += 1
            if concentrated:
                stats["concentrated"] += 1

        session.commit()
    finally:
        if own_session:
            session.close()

    return dict(stats)


if __name__ == "__main__":
    print(generate())
