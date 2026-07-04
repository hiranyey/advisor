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
  - Realistic saving, not fantasy: each goal's SIP is sized from its actual funding gap,
    horizon and expected return, then scaled by the client's savings discipline (mostly
    below 1x) and capped by what they can afford. Most clients under-save, so goal success
    probabilities spread across the whole range (median ~0.45) instead of everyone hitting
    every goal. Current funding is horizon-aware — you've barely started a 30-year goal.
  - Fully deterministic: a fixed RNG seed + truncate-and-reload means re-running seed.py
    reproduces the exact same book. Generate once, reuse forever.

Portfolio construction works in CURRENT-VALUE space: we pick target category weights,
convert to units via each fund's latest NAV, then back-date the buy(s) at that date's
historical NAV. So concentration/allocation are exact as the holdings view will show them.
"""

import bisect
import math
from collections import defaultdict
from datetime import date, timedelta
from random import Random

from sqlalchemy import text

from app.db import SessionLocal
from app.engine.market import FALLBACK_MU
from app.models import Client, Goal, GoalHolding, SipSchedule, Transaction

SEED = 42
N_CLIENTS = 173
BASE_DATE = date(2026, 7, 4)  # fixed "today" for reproducibility
PER_CATEGORY = 15             # funds sampled into the investable universe per category
MIN_HISTORY_DAYS = 500        # need ~2y of NAVs so back-dated buys can be priced
MIN_WEIGHT = 0.05             # a fund below this share of the book is merged away as dust

# ── Names ─────────────────────────────────────────────────────────────────────
# Deliberately large + regionally diverse so 150 clients read as distinct people.
# Full names are de-duplicated at generation time (see generate()), so no two
# clients share a name even when a first or last name recurs.
FIRST = [
    # younger / modern
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Krishna", "Ishaan",
    "Aryan", "Kabir", "Ayaan", "Shaurya", "Advik", "Rudra", "Ved", "Aarush",
    "Sarthak", "Rishabh", "Parth", "Tanish", "Neel", "Om", "Laksh", "Kian",
    "Ananya", "Diya", "Aadhya", "Saanvi", "Aanya", "Anika", "Navya", "Kavya",
    "Ishita", "Aarohi", "Myra", "Kiara", "Nitya", "Anvi", "Prisha", "Ira",
    "Aditi", "Shreya", "Riya", "Meera", "Pari", "Zara", "Tara", "Saira",
    # working-age / mature (clients run to 68)
    "Rahul", "Rohan", "Nikhil", "Karan", "Varun", "Siddharth", "Kunal", "Manav",
    "Yash", "Harsh", "Ronit", "Gaurav", "Pranav", "Aniket", "Rajat", "Nishant",
    "Rajesh", "Suresh", "Ramesh", "Mahesh", "Anil", "Sunil", "Vijay", "Ajay",
    "Sanjay", "Deepak", "Ashok", "Vikram", "Naveen", "Arvind", "Prakash", "Girish",
    "Priya", "Neha", "Pooja", "Divya", "Nisha", "Swati", "Anjali", "Kritika",
    "Sneha", "Deepika", "Bhavana", "Shalini", "Sunita", "Geeta", "Rekha", "Kavita",
    "Poonam", "Vidya", "Padma", "Latha", "Revathi", "Sowmya", "Anusha", "Lakshmi",
    "Radha", "Sushma", "Meenakshi", "Sarita", "Usha", "Jaya",
]
LAST = [
    "Sharma", "Verma", "Gupta", "Agarwal", "Bansal", "Mittal", "Jain", "Saxena",
    "Srivastava", "Tiwari", "Mishra", "Pandey", "Dubey", "Trivedi", "Chauhan", "Rathore",
    "Iyer", "Nair", "Menon", "Pillai", "Reddy", "Rao", "Naidu", "Hegde",
    "Shetty", "Bhat", "Kamath", "Prabhu", "Acharya", "Deshpande", "Gokhale", "Apte",
    "Kulkarni", "Joshi", "Bhatt", "Patel", "Shah", "Mehta", "Desai", "Trivedy",
    "Bose", "Chatterjee", "Banerjee", "Sen", "Ghosh", "Dutta", "Mukherjee", "Chakraborty",
    "Kapoor", "Malhotra", "Chopra", "Sinha", "Das", "Gill", "Sethi", "Khanna",
    "Bajaj", "Kohli", "Grewal", "Ahluwalia",
]

# Rarer, more distinctive names — classical, regional, or long compound surnames.
# Used for a minority of clients (see _pick_name) so the pool has some texture.
EXOTIC_FIRST = [
    "Satyajit", "Chiranjeev", "Digvijay", "Yashwardhan", "Prithviraj", "Devdatta",
    "Raghavendra", "Purushottam", "Shubhankar", "Tejaswin", "Anirudh", "Vishwanath",
    "Chandrashekhar", "Bhargav", "Ratnakar", "Devansh", "Ojas", "Vedant",
    "Yashodhara", "Damayanti", "Suhasini", "Padmavati", "Rukmini", "Chandrika",
    "Anasuya", "Kadambari", "Malavika", "Vasundhara", "Indrani", "Shakuntala",
    "Meghna", "Trishna", "Oorja", "Meenal", "Ahilya", "Sharmila",
]
EXOTIC_LAST = [
    "Mukhopadhyay", "Bandyopadhyay", "Gangopadhyay", "Chattopadhyay", "Chowdhury",
    "Deshmukh", "Gaikwad", "Bhonsle", "Sardesai", "Wadhwa",
    "Ranganathan", "Venkataraman", "Krishnamurthy", "Subramanian", "Balasubramanian",
    "Chidambaram", "Padmanabhan", "Thyagarajan", "Mahadevan", "Sreenivasan",
    "Varghese", "Kurup", "Namboothiri", "Panicker", "Vishwanathan",
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
        "high_risk_equity": 0.52, "mid_risk_equity": 0.28, "international_equity": 0.10,
        "silver": 0.05, "multi_asset": 0.05,
    },
    # Deliberately extreme — heavily one high-volatility category. A portfolio's simulated
    # worst-year drop is driven by its category mix (not fund count), so this is what pushes
    # a client into the −20/−30% "too risky" band on the radar. Kept for the mismatch demo.
    "very_aggressive": {
        "high_risk_equity": 0.82, "mid_risk_equity": 0.18,
    },
}

# STYLE_ALLOCATIONS is authored safest→riskiest, so its key order IS the risk ladder.
# A goal's time horizon nudges the client's style along it: money you need soon should
# sit in safer funds, money you won't touch for decades can ride more risk.
STYLE_LADDER = list(STYLE_ALLOCATIONS)

# Per stated risk_profile, the mix of ACTUAL styles clients end up with.
# The tails (e.g. conservative→very_aggressive) are the deliberate mismatches.
STYLE_BY_PROFILE = {
    "conservative": [
        ("safe", 0.50), ("conservative_growth", 0.25), ("balanced", 0.12),
        ("growth", 0.08), ("aggressive", 0.04), ("very_aggressive", 0.01),
    ],
    "balanced": [
        ("conservative_growth", 0.13), ("balanced", 0.40), ("growth", 0.20),
        ("safe", 0.07), ("aggressive", 0.14), ("very_aggressive", 0.06),
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


def _pick_weights(rng: Random, style, universe, exclude: set | None = None,
                  max_funds: int | None = None):
    """Choose funds and their CURRENT-VALUE weights for a style. -> [(fund_id, weight)].

    A heavy category (>18% target) is spread across two distinct funds so ordinary
    portfolios don't trip the single-fund concentration flag — that flag is reserved
    for the deliberately `concentrated` archetype (applied at portfolio level).

    `exclude` holds fund_ids already tagged to another of this client's goals; a fund
    tags at most one goal (goal_holdings PK), so we never reuse one across goals.

    `max_funds` caps the fund count so a small goal isn't sprayed across the whole
    style (which would make each fund a sub-5% sliver); we keep the largest few and
    renormalize."""
    exclude = exclude or set()
    alloc = STYLE_ALLOCATIONS[style]
    picks = []
    for cat, w in alloc.items():
        pool = [f for f in universe.get(cat, []) if f[0] not in exclude]
        if not pool:
            continue
        if w > 0.18 and len(pool) >= 2 and (max_funds is None or max_funds >= 4):
            f1, f2 = rng.sample(pool, 2)
            split = rng.uniform(0.4, 0.6)
            picks.append([f1[0], w * split])
            picks.append([f2[0], w * (1 - split)])
        else:
            picks.append([rng.choice(pool)[0], w])
    if max_funds is not None and len(picks) > max_funds:
        picks.sort(key=lambda x: x[1], reverse=True)
        picks = picks[:max_funds]
    total = sum(w for _, w in picks) or 1.0
    return [(fid, w / total) for fid, w in picks]


def _pick_name(rng: Random, seen: set) -> str:
    """A unique full name. ~14% of clients draw from the rarer EXOTIC pools so the
    book isn't all common names — a few classical / regional / compound-surname folks."""
    while True:
        if rng.random() < 0.14:
            name = f"{rng.choice(EXOTIC_FIRST)} {rng.choice(EXOTIC_LAST)}"
        else:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in seen:
            seen.add(name)
            return name


def _goal_style(rng: Random, base_style: str, years: float) -> str:
    """Tilt a client's style by a goal's time horizon, then return the goal's style.

    Near-term money (an emergency fund, a car in 2 years) is pulled toward safety;
    a retirement 20 years out is allowed to ride more risk. About 1 in 8 goals ignores
    the horizon (a short goal left in equity, a long goal parked in debt) — the "for no
    reason" mismatches the Book Risk Radar is meant to surface."""
    idx = STYLE_LADDER.index(base_style)
    if years <= 3:
        shift = -2          # emergency / very short → markedly safer
    elif years <= 7:
        shift = -1          # short–medium → a notch safer
    elif years <= 12:
        shift = 0           # medium → as-is
    else:
        shift = +1          # long horizon → can lean riskier
    if rng.random() < 0.12:
        shift = rng.choice([-2, -1, 1, 2])  # deliberate horizon-blind mismatch
    idx = max(0, min(len(STYLE_LADDER) - 1, idx + shift))
    return STYLE_LADDER[idx]


def _draw_progress(rng: Random, years: float) -> float:
    """How funded a goal is *today*, as a fraction of its target — HORIZON-AWARE.

    You've barely started a 30-year retirement but a car 2 years out is well underway,
    so the funded fraction is capped by the time you've had to save. This also keeps the
    engine honest: without it, a long goal's small current holding compounds (25y at ~12%
    is ~17×) straight past the target, making every long goal a trivial 100%. Drawn skewed
    toward the low end of each horizon's band, so goals read as work-in-progress."""
    if years <= 3:
        lo, hi = 0.20, 1.10      # near-term: can be nearly (or already) funded
    elif years <= 7:
        lo, hi = 0.12, 0.80
    elif years <= 15:
        lo, hi = 0.08, 0.55
    else:
        lo, hi = 0.05, 0.35      # decades out: only just begun
    r = rng.random() ** 1.6      # bias toward the lower end of the band
    return lo + (hi - lo) * r


def _portfolio_cap(age: int | None) -> float | None:
    """Age-appropriate ceiling on total current portfolio value (₹), or None.

    Wealth accumulates with a career, so a 26-year-old holding ₹3Cr reads as fake.
    Younger clients are capped hard: nobody under 35 exceeds ₹1Cr."""
    if age is None:
        return None
    if age < 30:
        return 4.0e6    # ₹40L
    if age < 35:
        return 1.0e7    # ₹1Cr
    if age < 45:
        return 3.0e7    # ₹3Cr
    return None         # established investors: goal targets alone bound it


def _merge_dust(holdings, floor: float = MIN_WEIGHT):
    """Merge away dust holdings so each fund is a meaningful slice of the book.

    Building per-goal across a style's many categories can leave a client holding
    20+ funds at <1% each — noise that also spawns pointless SIPs. Within each goal
    we fold sub-`floor` funds proportionally into that goal's larger funds, so the
    goal's funded value is unchanged. A goal whose *entire* budget is below the floor
    keeps just its single largest fund — a genuine outlier, not the norm.

    holdings: [[fund_id, goal, value], ...]. Returns the pruned list."""
    total = sum(h[2] for h in holdings)
    if total <= 0:
        return holdings
    # Small margin so survivors clear the floor cleanly rather than landing on it
    # (float drift from redistribution/scaling would otherwise dip them just under).
    cutoff = floor * 1.05 * total
    by_goal = defaultdict(list)
    for h in holdings:
        by_goal[h[1]].append(h)  # goal object, hashable by identity
    kept = []
    for group in by_goal.values():
        group.sort(key=lambda h: h[2])
        while len(group) > 1 and group[0][2] < cutoff:
            dust = group.pop(0)
            rest_sum = sum(h[2] for h in group) or 1.0
            for h in group:
                h[2] += dust[2] * h[2] / rest_sum
            group.sort(key=lambda h: h[2])
        kept.extend(group)
    return kept


# A single buy shouldn't dwarf the book. Positions are accumulated over several tranches,
# each targeting roughly this much of today's value, so no one transaction is a giant
# lumpsum. Small positions still land in one buy (which is small in absolute terms).
TRANCHE_VALUE_LO = 250_000     # ~₹2.5L
TRANCHE_VALUE_HI = 600_000     # ~₹6L
MAX_TRANCHES = 8

# Fraction of positions that also carry a partial redeem (a realistic mid-life trim).
# We over-buy up-front and sell the excess later, so the NET position still values to
# target_value — the derived holdings view is unchanged, only the ledger gains a sell.
SELL_PROB = 0.12


def _make_buys(rng: Random, series, fid, target_value):
    """Back-dated tranches whose NET current value == target_value.
    -> [(date, type, units, nav, amount)], type in {"buy", "redeem"}.

    The number of tranches scales with the position's size: a large holding is built up
    over several buys spread across the accumulation window (like real investing), never a
    single abrupt lumpsum. Tranche sizes are jittered and dates sorted so a position
    accumulates over time.

    ~SELL_PROB of positions additionally trim part of the position: we buy extra units
    up-front and redeem them at a later date, so the leftover NET units still equal
    target_value / latest_nav — holdings/allocation/concentration are untouched, the
    ledger just shows a genuine sell."""
    ds, ns = series[fid]
    latest_nav = ns[-1]
    net_units = target_value / latest_nav

    earliest = ds[0] + timedelta(days=30)
    horizon = BASE_DATE - timedelta(days=int(rng.uniform(365, 6 * 365)))
    lo = max(earliest, horizon)
    hi = BASE_DATE - timedelta(days=30)
    if lo >= hi:
        lo = hi - timedelta(days=30)

    # Decide on a partial trim: over-buy `sell_units`, then redeem them later. Net stays
    # at net_units (> 0), so the holdings view is identical to a buy-only position.
    sell_units = net_units * rng.uniform(0.15, 0.40) if rng.random() < SELL_PROB else 0.0
    total_units = net_units + sell_units

    # Tranche count grows with size; capped so we don't spray into dozens of tiny buys.
    per_tranche = rng.uniform(TRANCHE_VALUE_LO, TRANCHE_VALUE_HI)
    n = max(1, min(MAX_TRANCHES, round(target_value / per_tranche)))
    if sell_units > 0:
        n = max(n, 2)  # need at least one buy on the books before we can redeem

    # Unequal-ish split of the units across tranches (weights sum to 1).
    weights = [rng.uniform(0.6, 1.4) for _ in range(n)]
    wsum = sum(weights)
    weights = [w / wsum for w in weights]

    # Spread the buy dates across the window and sort so the position accumulates over time.
    span = (hi - lo).days
    offsets = sorted(rng.randrange(span) for _ in range(n)) if span > 1 else [0] * n

    rows = []
    allocated = 0.0
    buy_dates, cum_units = [], []
    for i, (w, off) in enumerate(zip(weights, offsets)):
        # Last tranche takes the exact remainder so units sum to total_units precisely.
        units = total_units - allocated if i == n - 1 else total_units * w
        allocated += units
        nav_date, nav = _nav_on_or_before(series, fid, lo + timedelta(days=off))
        rows.append((nav_date, "buy", round(units, 4), round(nav, 4), round(units * nav, 2)))
        buy_dates.append(nav_date)
        cum_units.append(allocated)

    if sell_units > 0:
        # Redeem after the earliest tranche that already covers sell_units (can't sell
        # units not yet held), and before today.
        k = next((idx for idx, c in enumerate(cum_units) if c >= sell_units), n - 1)
        sell_lo = buy_dates[k] + timedelta(days=15)
        sell_hi = BASE_DATE - timedelta(days=5)
        if sell_lo >= sell_hi:
            sell_lo = sell_hi - timedelta(days=5)
        off = rng.randrange((sell_hi - sell_lo).days) if sell_hi > sell_lo else 0
        nav_date, nav = _nav_on_or_before(series, fid, sell_lo + timedelta(days=off))
        rows.append((nav_date, "redeem", round(sell_units, 4),
                     round(nav, 4), round(sell_units * nav, 2)))
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


def _monthly_savings_capacity(rng: Random, age: int, portfolio_value: float) -> float:
    """A plausible ceiling (₹/month) on what this client can actually invest.

    Anchored to an age-based income proxy (earnings peak in the 40s–early 50s), widened
    by a personal spread, and lifted for the wealthy (~0.5%/month of the current pot).
    Clamped to a sane band. This is what stops goals being trivially funded: when the SIP
    a goal *needs* exceeds what the client can afford, the goal simply stays off-track —
    exactly how real under-saving shows up."""
    if age < 30:
        base = 22_000
    elif age < 40:
        base = 45_000
    elif age < 55:
        base = 75_000
    else:
        base = 50_000
    base *= rng.uniform(0.5, 1.6)
    base = max(base, portfolio_value * 0.005)  # affluent clients can commit more
    return float(min(max(base, 2_000), 300_000))


def _savings_discipline(rng: Random) -> float:
    """How much of the *required* SIP a client actually contributes, as a fraction.

    Skewed below 1.0: most people under-save for their goals, some are on track, a
    disciplined minority save ahead. Drawn once per client (a saver saves across all
    their goals); each goal then jitters around it. This factor is the main lever on the
    spread of goal success probabilities — median ~0.8 keeps the book realistically mixed
    rather than everyone reaching every goal."""
    r = rng.random()
    if r < 0.15:
        return rng.uniform(0.40, 0.70)   # behind
    if r < 0.50:
        return rng.uniform(0.70, 1.10)   # a bit short
    if r < 0.82:
        return rng.uniform(1.10, 1.40)   # roughly on track
    return rng.uniform(1.40, 1.90)       # disciplined / ahead


def _required_monthly_sip(current_value: float, target: float, g_mu: float,
                          n_months: int) -> float:
    """The level monthly SIP that, with current holdings growing at the goal's expected
    return `g_mu`, is projected to just reach `target` by the horizon. 0 if holdings alone
    already get there. Uses the same expected monthly growth the engine implies
    (E[step] = e^(mu/12)) so 'adequacy = 1.0' lands a goal near a coin-flip before its
    own volatility and allocation tilt it either way."""
    r = math.exp(g_mu / 12) - 1
    grown_holdings = current_value * math.exp(g_mu * n_months / 12)
    gap = target - grown_holdings
    if gap <= 0:
        return 0.0
    annuity = ((1 + r) ** n_months - 1) / r if r > 1e-9 else n_months
    return gap / annuity


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

        fund_cat = {fid: cat for cat, funds in universe.items() for fid, _ in funds}

        seen_names: set[str] = set()

        for i in range(N_CLIENTS):
            profile, style, concentrated = _client_plan(rng, i, universe)

            name = _pick_name(rng, seen_names)
            age = rng.randint(24, 68)
            client = Client(name=name, age=age, risk_profile=profile)
            session.add(client)

            goals_spec = _make_goals(rng)
            goal_objs = []
            for gname, amount, target in goals_spec:
                g = Goal(client=client, name=gname, target_amount=amount, target_date=target)
                session.add(g)
                goal_objs.append(g)

            # ── Portfolio is DERIVED from the goals ────────────────────────────
            # Each goal is funded to a random fraction of its target (mostly
            # partial), and only the funds bought *for* that goal are tagged to it.
            # So funded_value/target reads as real progress, and total portfolio
            # value falls out of the goals instead of being an unrelated number.
            # Per-goal current funding = target × progress. Then lift any goal that
            # would sit below the book's 5% dust floor up to that floor — but never
            # above its own target — so a goal only yields a sub-5% sliver when the
            # goal itself is that small next to its siblings (a rare true outlier).
            budgets = [
                float(amount) * _draw_progress(rng, (target - BASE_DATE).days / 365.0)
                for _, amount, target in goals_spec
            ]
            caps = [float(amount) for _, amount, _ in goals_spec]  # never fund past target
            # Iterate: lifting a goal to the 5% floor raises the book, which raises the
            # floor — a few passes converge so every liftable goal truly clears 5%.
            for _ in range(4):
                book = sum(budgets) or 1.0
                budgets = [max(b, min(MIN_WEIGHT * book, cap))
                           for b, cap in zip(budgets, caps)]

            total_budget = sum(budgets) or 1.0
            holdings: list[list] = []  # [fund_id, goal_obj, current_value]
            used_funds: set[int] = set()
            for goal, budget, (_, _, target) in zip(goal_objs, budgets, goals_spec):
                # Fund category follows the goal's horizon, not just the client's style:
                # short-dated goals lean safe, long-dated goals can lean risky.
                years = (target - BASE_DATE).days / 365.0
                goal_style = _goal_style(rng, style, years)
                # Cap funds per goal so each is a real slice. We size against a target
                # weight above the 5% floor (funds within a goal are unevenly weighted,
                # so the *smallest* needs headroom to still clear the floor).
                max_funds = max(1, int((budget / total_budget) / (MIN_WEIGHT * 1.5)))
                for fid, w in _pick_weights(rng, goal_style, universe,
                                            exclude=used_funds, max_funds=max_funds):
                    used_funds.add(fid)
                    holdings.append([fid, goal, budget * w])

            # Merge dust: fold sub-5% funds into larger siblings of the same goal, so
            # a client holds a handful of meaningful funds (and SIPs) rather than 20+.
            holdings = _merge_dust(holdings)

            # Age-appropriate ceiling: scale the whole book down if the goal-derived
            # value outruns what someone this age would plausibly have accumulated.
            cap = _portfolio_cap(age)
            if cap is not None and holdings:
                total = sum(h[2] for h in holdings)
                if total > cap:
                    scale = cap * rng.uniform(0.6, 0.95) / total
                    for h in holdings:
                        h[2] *= scale

            # Concentration archetype: one fund is meaningfully overweight (enough to trip
            # the >25% single-fund flag) — but not so dominant it IS the whole portfolio.
            if concentrated and holdings:
                total = sum(h[2] for h in holdings) or 1.0
                big = rng.uniform(0.35, 0.50)
                j = max(range(len(holdings)), key=lambda k: holdings[k][2])  # bloat the largest
                rest = sum(holdings[k][2] for k in range(len(holdings)) if k != j) or 1.0
                for k in range(len(holdings)):
                    holdings[k][2] = total * (big if k == j else holdings[k][2] / rest * (1 - big))

            # ── Buys + goal tags (per fund) ────────────────────────────────────
            for fid, goal, value in holdings:
                if value <= 0:
                    continue
                for nav_date, txn_type, units, nav, amount in _make_buys(rng, series, fid, value):
                    if units <= 0:
                        continue
                    session.add(Transaction(
                        client=client, fund_id=fid, date=nav_date, type=txn_type,
                        units=units, nav=nav, amount=amount,
                    ))
                    stats["transactions"] += 1

                session.add(GoalHolding(client=client, fund_id=fid, goal=goal))
                stats["goal_holdings"] += 1

            # ── SIPs: sized per goal from its gap, horizon and expected return ──
            # Each goal's monthly SIP is the amount projected to close its funding gap by
            # the target date, scaled by the client's savings discipline (mostly < 1, so
            # most goals stay a bit short) and then capped by what the client can actually
            # afford. A fund's SIP is that goal's SIP split across its funds by weight.
            portfolio_value = sum(h[2] for h in holdings if h[2] > 0)
            capacity = _monthly_savings_capacity(rng, age, portfolio_value)
            discipline = _savings_discipline(rng)

            goal_funds: dict = defaultdict(list)  # goal_obj -> [(fund_id, value)]
            for fid, goal, value in holdings:
                if value > 0:
                    goal_funds[goal].append((fid, value))

            goal_sip: dict = {}  # goal_obj -> desired total monthly SIP (pre-affordability)
            for goal in goal_objs:
                funds = goal_funds.get(goal)
                if not funds:
                    continue
                cur_val = sum(v for _, v in funds)
                g_mu = sum(v * FALLBACK_MU.get(fund_cat.get(fid, "other"), 0.08)
                           for fid, v in funds) / cur_val
                years = max((goal.target_date - BASE_DATE).days / 365.0, 0.5)
                n_months = max(int(round(years * 12)), 1)
                req = _required_monthly_sip(cur_val, float(goal.target_amount), g_mu, n_months)
                if req <= 0:
                    continue  # holdings already project to the target — no SIP needed
                adequacy = discipline * rng.uniform(0.7, 1.3)
                goal_sip[goal] = req * adequacy

            # Affordability cap: scale all goals down together if the client can't fund them.
            desired = sum(goal_sip.values())
            if desired > capacity and desired > 0:
                scale = capacity / desired
                goal_sip = {g: s * scale for g, s in goal_sip.items()}

            # Materialize: split each goal's SIP across its funds by weight; skip dust.
            for goal, sip_total in goal_sip.items():
                funds = goal_funds[goal]
                cur_val = sum(v for _, v in funds) or 1.0
                stepup = rng.choice([0, 0, 0, 0.05, 0.10])
                active = rng.random() < 0.9
                start = BASE_DATE - timedelta(days=rng.randrange(30, 3 * 365))
                for fid, value in funds:
                    amount = round(sip_total * value / cur_val, -2)
                    if amount < 500:  # sub-₹500 SIPs aren't worth modeling
                        continue
                    session.add(SipSchedule(
                        client=client, fund_id=fid, monthly_amount=amount,
                        stepup_rate=stepup, start_date=start, active=active,
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
