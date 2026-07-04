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
        "high_risk_equity": 0.40, "mid_risk_equity": 0.25, "international_equity": 0.15,
        "silver": 0.05, "multi_asset": 0.10, "low_risk_equity": 0.05,
    },
    "very_aggressive": {
        "high_risk_equity": 0.55, "mid_risk_equity": 0.25, "international_equity": 0.15,
        "silver": 0.05,
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


def _draw_progress(rng: Random) -> float:
    """How funded a goal is *today*, as a fraction of its target. Drawn per goal.

    Heavily weighted toward partial funding — a goal is meant to be a work in
    progress the Monte Carlo engine projects forward, not an already-solved sum.
    Only ~3% land ahead of target (>1.05). Because each of a client's goals draws
    independently, a client whose *every* goal is already met is genuinely rare."""
    r = rng.random()
    if r < 0.35:
        return rng.uniform(0.05, 0.25)   # just getting started
    if r < 0.70:
        return rng.uniform(0.25, 0.55)   # underway
    if r < 0.88:
        return rng.uniform(0.55, 0.85)   # well along
    if r < 0.97:
        return rng.uniform(0.85, 1.05)   # nearly / just there
    return rng.uniform(1.05, 1.45)       # ahead of schedule (rare)


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
            budgets = [float(amount) * _draw_progress(rng) for _, amount, _ in goals_spec]
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

            # Concentration archetype: one fund dominates the WHOLE portfolio.
            if concentrated and holdings:
                total = sum(h[2] for h in holdings) or 1.0
                big = rng.uniform(0.55, 0.72)
                j = max(range(len(holdings)), key=lambda k: holdings[k][2])  # bloat the largest
                rest = sum(holdings[k][2] for k in range(len(holdings)) if k != j) or 1.0
                for k in range(len(holdings)):
                    holdings[k][2] = total * (big if k == j else holdings[k][2] / rest * (1 - big))

            for fid, goal, value in holdings:
                if value <= 0:
                    continue
                for nav_date, units, nav, amount in _make_buys(rng, series, fid, value):
                    if units <= 0:
                        continue
                    session.add(Transaction(
                        client=client, fund_id=fid, date=nav_date, type="buy",
                        units=units, nav=nav, amount=amount,
                    ))
                    stats["transactions"] += 1

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
