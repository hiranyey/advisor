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
      · needs-attention — over-exposed AND badly under-saving, so they land bottom-right
                       on the Risk Radar quadrant (too much risk + low goal odds), not
                       just off to one side of it
  - SIP first, transactions second: a client's realistic monthly saving (age-based income
    proxy × a savings-discipline factor, mostly < 1x — most people under-save) IS the
    number that lands on the SipSchedule row. Past transactions are then simulated as that
    SAME monthly amount, grown ~6%/year back over the client's own investing tenure (mostly
    2.5–6y, a minority 8–15y for the "started long ago" cases) and bought at real historical
    NAVs. Current value falls out of genuine market moves on a genuine contribution history
    — it never drifts off to a number unrelated to what the SIP says they invest.
  - Most clients under-save relative to their goals, so goal success probabilities spread
    across the whole range instead of everyone hitting every goal — a goal's funding is
    whatever this bottom-up contribution history actually produces, not an assumed fraction
    of its target.
  - Fully deterministic: a fixed RNG seed + truncate-and-reload means re-running seed.py
    reproduces the exact same book. Generate once, reuse forever.

Portfolio construction works in MONTHLY-SIP space: we pick target category weights, size
each fund's slice of the client's SIP, then back-date a growing contribution history at
each date's real historical NAV. Current value/allocation/concentration are whatever falls
out of that — never an independently chosen number.
"""

import bisect
import math
from collections import defaultdict
from datetime import date, timedelta
from random import Random

from sqlalchemy import text

from sim_kernel.state import FALLBACK_MU

from app.db import SessionLocal
from app.models import Client, Goal, GoalHolding, SipSchedule, Transaction

SEED = 42
N_CLIENTS = 173
# `_required_monthly_sip` assumes a level payment the WHOLE span; `_make_sip_buys`
# actually ramps up from a lower level over the historical portion (see ANNUAL_GROWTH),
# so a client's true average contribution runs below that ideal — and the real
# Monte Carlo engine's success probability is a tail estimate on a compounding,
# volatile path, not the naive expected-value trajectory this script can replicate
# exactly. This constant is an empirical correction (tuned against `run_book_analysis`
# output, not derived) so `discipline`'s intended spread survives contact with the real
# engine instead of reading uniformly harsher than intended.
REQUIRED_SIP_CALIBRATION = 1.4
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
# Gold/silver are kept as a small satellite sliver (not the old 10%/5%) — this book's
# derived market model has silver/gold appreciating far faster than every other category
# over the back-dated history (a real feature of the underlying NAV data, not a modeling
# choice), so even a modest target weight compounds into an outsized CURRENT-value share
# book-wide. A real advisor's book reads as equity/debt at the top; metals should be a
# footnote, not neck-and-neck with them.
STYLE_ALLOCATIONS = {
    "safe": {
        "cash_equivalent": 0.25, "good_debt": 0.43, "conservative_hybrid": 0.20,
        "gold": 0.05, "low_risk_equity": 0.07,
    },
    "conservative_growth": {
        "good_debt": 0.33, "conservative_hybrid": 0.20, "balanced_advantage": 0.20,
        "low_risk_equity": 0.17, "gold": 0.05, "cash_equivalent": 0.05,
    },
    "balanced": {
        "balanced_advantage": 0.20, "aggressive_hybrid": 0.20, "low_risk_equity": 0.17,
        "mid_risk_equity": 0.17, "good_debt": 0.16, "gold": 0.05, "international_equity": 0.05,
    },
    "growth": {
        "mid_risk_equity": 0.32, "low_risk_equity": 0.15, "high_risk_equity": 0.17,
        "aggressive_hybrid": 0.15, "international_equity": 0.11, "gold": 0.05, "multi_asset": 0.05,
    },
    "aggressive": {
        # No silver here (unlike the old version): silver's realized CAGR in this book's
        # NAV history is so far above every other category (~27% vs ~11-15%) that even a
        # 2% sliver compounds into an outsized share for an older, long-tenured, no-cap
        # (age 45+) client — small allocation, disproportionate current-value footprint.
        "high_risk_equity": 0.55, "mid_risk_equity": 0.30, "international_equity": 0.12,
        "multi_asset": 0.03,
    },
    # Deliberately extreme — heavily one high-volatility equity category. Still not
    # actually enough to breach the Risk Radar's tolerance bands (see "reckless" below):
    # diversified equity categories run ~14-15% annual vol in this book's derived market
    # model, well short of what a 10-35% drawdown tolerance needs. Kept as the "very
    # equity-heavy but technically still just equity" rung of the ladder.
    "very_aggressive": {
        "high_risk_equity": 0.82, "mid_risk_equity": 0.18,
    },
    # The two styles that actually trip the suitability-mismatch flag — silver/gold are
    # the only categories volatile enough (~32%/~17% annual vol vs ~15% for equity) to
    # push a 1-year 95% VaR past the conservative/balanced tolerance bands (not the
    # *aggressive* 35% band — nothing in this book's derived market model gets there).
    # Their returns correlate negatively with equity (the classic gold/silver hedge), so
    # diluting either with equity to look "more like a real portfolio" mostly cancels the
    # effect instead of adding to it — pairing them with each other instead preserves the
    # breach while still reading as a two-fund book, not one giant metal bet. Two distinct
    # styles (silver-led vs gold-led) so the book's "why call them" reasons don't all cite
    # the same metal. Used only by the deliberate-mismatch archetypes.
    "reckless": {                    # ~26% VaR — clears conservative (10%) AND balanced (20%)
        "silver": 0.90, "gold": 0.10,
    },
    "reckless_gold": {               # ~17% VaR — clears conservative (10%) only
        "gold": 0.90, "silver": 0.10,
    },
}

# The ordinary risk ladder — safest to riskiest. A goal's time horizon nudges a client's
# style along it: money you need soon should sit in safer funds, money you won't touch
# for decades can ride more risk. Deliberately excludes "reckless"/"reckless_gold": those
# are hand-assigned to specific archetype clients only (see _client_plan) and must never
# be reachable by the ordinary horizon-drift shift in _goal_style — an organic client
# landing there by chance defeats the whole point of keeping their book-wide footprint
# small and deliberate.
STYLE_LADDER = [
    "safe", "conservative_growth", "balanced", "growth", "aggressive", "very_aggressive",
]

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


# Assumed year-over-year rise in what a client contributes, independent of any
# *scheduled* future step-up rate on the SIP row — plain income growth over a career.
# Drives the shape of the back-dated tranches in `_make_sip_buys`.
ANNUAL_GROWTH = 0.06

# Fraction of positions that also carry a partial redeem (a realistic mid-life trim).
SELL_PROB = 0.12


def _make_sip_buys(rng: Random, series, fid, monthly_amount_today: float, invest_years: float):
    """This fund's slice of the client's SIP, simulated as a growing series of yearly
    lumpsum tranches back-dated at real historical NAVs.
    -> [(date, type, units, nav, amount)], type in {"buy", "redeem"}.

    `monthly_amount_today` is the SAME number that becomes this fund's SipSchedule row —
    transactions are DERIVED from the SIP, not the other way around. Tranche k grows
    ~ANNUAL_GROWTH per year toward that level (contributions rising with income, even when
    the *scheduled* future step-up is 0%), so current value is the genuine result of a
    growing contribution history hitting real market moves — it stays anchored to the SIP
    amount instead of drifting off to an unrelated figure.

    ~SELL_PROB of positions also carry a later partial redeem, for ledger texture."""
    if monthly_amount_today <= 0:
        return []
    ds, _ = series[fid]
    earliest = ds[0] + timedelta(days=30)
    start = max(BASE_DATE - timedelta(days=int(round(invest_years * 365))), earliest)
    end = BASE_DATE - timedelta(days=30)
    span = max((end - start).days, 30)
    n_years = max(1, min(12, round(invest_years)))

    rows = []  # [date, type, units, nav]
    total_units = 0.0
    buy_dates, cum_units = [], []
    for k in range(n_years):
        # k=0 is the oldest year; k=n_years-1 is "this year", landing at today's level.
        level = monthly_amount_today / ((1 + ANNUAL_GROWTH) ** (n_years - 1 - k))
        yearly_amount = level * 12 * rng.uniform(0.85, 1.15)
        off = int(span * (k + rng.uniform(0.15, 0.85)) / n_years)
        nav_date, nav = _nav_on_or_before(series, fid, start + timedelta(days=min(off, span)))
        units = yearly_amount / nav
        rows.append([nav_date, "buy", units, nav])
        total_units += units
        buy_dates.append(nav_date)
        cum_units.append(total_units)

    if len(rows) >= 2 and rng.random() < SELL_PROB:
        sell_units = total_units * rng.uniform(0.15, 0.40)
        k = next((idx for idx, c in enumerate(cum_units) if c >= sell_units), len(rows) - 1)
        sell_lo = buy_dates[k] + timedelta(days=15)
        sell_hi = BASE_DATE - timedelta(days=5)
        if sell_lo >= sell_hi:
            sell_lo = sell_hi - timedelta(days=5)
        off = rng.randrange((sell_hi - sell_lo).days) if sell_hi > sell_lo else 0
        nav_date, nav = _nav_on_or_before(series, fid, sell_lo + timedelta(days=off))
        rows.append([nav_date, "redeem", sell_units, nav])

    return [(d, t, round(u, 4), round(n, 4), round(u * n, 2)) for d, t, u, n in rows]


def _make_goals(rng: Random, force_names: list[str] | None = None):
    """1–3 distinct goals. -> [(name, target_amount, target_date)].

    `force_names` guarantees those goal types are always included (used for the
    needs-attention archetype: a low SIP can drift into funding a small, short goal by
    accident, so we pin at least one genuinely large, long-horizon goal a modest
    contribution can't realistically catch up on)."""
    n = max(rng.choice([1, 1, 2, 2, 2, 3]), len(force_names or []))
    n = min(n, len(GOAL_TYPES))
    forced = list(dict.fromkeys(force_names or []))
    remaining_pool = [g for g in GOAL_TYPES if g not in forced]
    extra = rng.sample(remaining_pool, max(0, n - len(forced)))
    names = forced + extra
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


def _pick_invest_years(rng: Random, age: int) -> float:
    """How many years ago this client's oldest holding began.

    Most clients read as having invested for the last few years (matching the book's
    historical default). A minority — skewed toward clients old enough to have had a
    longer career runway — started much earlier, so the book isn't uniformly "everyone
    began investing around the same time." Bounded so nobody's history predates a
    plausible working age (~22)."""
    max_possible = max(1.5, min(20.0, age - 22))
    if rng.random() < 0.18:
        lo, hi = 8.0, 15.0
    else:
        lo, hi = 2.5, 6.0
    hi = min(hi, max_possible)
    lo = min(lo, hi)
    return rng.uniform(lo, hi)


def _style_mu(style: str) -> float:
    """A style's blended expected annual return, from its category weights."""
    return sum(w * FALLBACK_MU.get(cat, 0.08) for cat, w in STYLE_ALLOCATIONS[style].items())


def _required_monthly_sip(target: float, g_mu: float, n_months: int) -> float:
    """The level monthly SIP that, compounding at `g_mu` from zero, lands exactly on
    `target` after `n_months`. Paying less than this (a discipline factor < 1) therefore
    lands proportionally short of the target — `discipline * required` compounds to
    `discipline * target` — regardless of how long `n_months` is, so a harder/longer
    goal doesn't quietly become easier just because compounding has longer to work."""
    r = math.exp(g_mu / 12) - 1
    annuity = ((1 + r) ** n_months - 1) / r if r > 1e-9 else n_months
    return target / annuity if annuity > 0 else target


def _client_plan(rng: Random, i: int, universe):
    """Decide one client's profile, style and edge-case flags."""
    # Force a visible block of extreme cases so the demo always has them.
    if i < 6:                       # severe suitability mismatch — alternate silver-led
                                     # and gold-led so these 6 don't all cite the same
                                     # metal as "the reason to call".
        profile = "conservative"
        style = "reckless" if i % 2 == 0 else "reckless_gold"
        concentrated, needs_attention = False, False
    elif i < 12:                    # concentration flag
        profile = _weighted(rng, PROFILE_WEIGHTS)
        style, concentrated, needs_attention = "aggressive", True, False
    elif i < 24:                    # needs-attention: over-exposed AND badly behind on goals —
                                     # a metal-heavy style (mostly) is what actually lands
                                     # them bottom-right ("Needs attention"), not just at
                                     # the bottom. "reckless_gold" only clears the
                                     # *conservative* tolerance (~17% VaR), so balanced
                                     # clients need the stronger silver-led "reckless"
                                     # (~26%) to clear their wider 20% band — keeping both
                                     # in rotation (rather than silver for everyone) is
                                     # what keeps the call list from reading as one story.
        profile = _weighted(rng, [("conservative", 0.5), ("balanced", 0.5)])
        if profile == "conservative":
            style = rng.choice(["reckless", "reckless_gold", "reckless_gold", "very_aggressive"])
        else:
            style = rng.choice(["reckless", "reckless", "reckless", "very_aggressive"])
        concentrated = rng.random() < 0.3
        needs_attention = True
    else:                           # the organic distribution
        profile = _weighted(rng, PROFILE_WEIGHTS)
        style = _weighted(rng, STYLE_BY_PROFILE[profile])
        concentrated = rng.random() < 0.08
        needs_attention = False
    return profile, style, concentrated, needs_attention


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
            profile, style, concentrated, needs_attention = _client_plan(rng, i, universe)

            name = _pick_name(rng, seen_names)
            # A "reckless" (silver/gold-led) client stays YOUNG, so `_portfolio_cap`
            # keeps their absolute rupee exposure small — the point is a client whose
            # ALLOCATION is dangerously concentrated, not one whose metal bet is big
            # enough to distort the whole book's category totals.
            if style in ("reckless", "reckless_gold"):
                age = rng.randint(24, 29)  # strictly < 30 — the tightest _portfolio_cap bracket
            else:
                age = rng.randint(24, 68)
            client = Client(name=name, age=age, risk_profile=profile)
            session.add(client)

            goals_spec = _make_goals(rng, force_names=["Retirement"] if needs_attention else None)
            goal_objs = []
            for gname, amount, target in goals_spec:
                g = Goal(client=client, name=gname, target_amount=amount, target_date=target)
                session.add(g)
                goal_objs.append(g)

            # ── SIP first: size it from what each goal actually needs ──────────────
            # required_monthly is the level SIP that, compounding at the goal's expected
            # return over the client's FULL span (their invest_years of history plus the
            # years still to go — the same span `_make_sip_buys` back-dates over), lands
            # exactly on the target. Paying `required * discipline` therefore lands
            # (roughly, ignoring volatility) on `target * discipline` — so discipline < 1
            # is a REAL shortfall, however long the horizon, not just a smaller but still
            # comfortable number. This is what stops long goals being trivially easy: a
            # bigger/harder goal has a bigger required_monthly, so the same discipline
            # produces a proportionally bigger shortfall, not an easier one.
            invest_years = _pick_invest_years(rng, age)
            # No portfolio value exists yet to anchor the affluence boost in
            # `_monthly_savings_capacity` — goal size is the available proxy for it.
            goal_value_proxy = sum(float(amount) for _, amount, _ in goals_spec)
            capacity = _monthly_savings_capacity(rng, age, goal_value_proxy)
            if needs_attention:
                # Severely under-saving, not just "a bit short" — this is what makes a
                # goal genuinely fail rather than merely read as somewhat behind.
                discipline = rng.uniform(0.05, 0.15)
            else:
                discipline = _savings_discipline(rng)

            goal_style_of: dict = {}   # goal_obj -> style used for its funds
            goal_monthly: dict = {}    # goal_obj -> desired monthly SIP (pre-affordability)
            for goal, (_, amount, target) in zip(goal_objs, goals_spec):
                years = max((target - BASE_DATE).days / 365.0, 0.25)
                # needs_attention clients, and anyone deliberately put in a "reckless*"
                # style, stay genuinely over-exposed on EVERY goal — skipping the horizon
                # tilt is what makes their simulated drawdown actually breach tolerance
                # and land them on the Risk Radar, instead of a short-horizon goal
                # quietly diluting it back down the ladder into an unremarkable mix.
                is_reckless = style in ("reckless", "reckless_gold")
                goal_style = style if (needs_attention or is_reckless) else _goal_style(rng, style, years)
                goal_style_of[goal] = goal_style
                g_mu = _style_mu(goal_style)
                span_months = max(int(round((invest_years + years) * 12)), 1)
                required = _required_monthly_sip(float(amount), g_mu, span_months)
                adequacy = discipline * REQUIRED_SIP_CALIBRATION * rng.uniform(0.8, 1.2)
                goal_monthly[goal] = required * adequacy

            # Affordability cap: scale all goals down together if the client can't fund
            # them combined — a secondary check; `discipline` above is the main lever.
            desired_total = sum(goal_monthly.values())
            if desired_total > capacity and desired_total > 0:
                aff_scale = capacity / desired_total
                goal_monthly = {g: v * aff_scale for g, v in goal_monthly.items()}
            total_monthly = sum(goal_monthly.values())

            # ── Pick funds per goal, size each fund's slice of the SIP ──────────────
            fund_monthly: list[list] = []  # [fund_id, goal_obj, monthly_amount]
            used_funds: set[int] = set()
            for goal in goal_objs:
                g_monthly = goal_monthly.get(goal, 0.0)
                if g_monthly <= 0:
                    continue
                goal_style = goal_style_of[goal]
                share = g_monthly / total_monthly if total_monthly > 0 else 0.0
                max_funds = max(1, int(share / (MIN_WEIGHT * 1.5)))
                for fid, w in _pick_weights(rng, goal_style, universe,
                                            exclude=used_funds, max_funds=max_funds):
                    used_funds.add(fid)
                    fund_monthly.append([fid, goal, g_monthly * w])

            # Merge dust: fold sub-5% (of total_monthly) funds into larger siblings of
            # the same goal, so a client holds a handful of meaningful SIPs, not 20+.
            fund_monthly = _merge_dust(fund_monthly)

            # Concentration archetype: one fund draws a meaningfully overweight share of
            # the WHOLE SIP (enough to trip the >25% single-fund flag once compounded)
            # — applied here, so both the historical buys and the recorded SIP for that
            # fund are concentrated together, not just one or the other.
            if concentrated and fund_monthly:
                total = sum(h[2] for h in fund_monthly) or 1.0
                big = rng.uniform(0.35, 0.50)
                j = max(range(len(fund_monthly)), key=lambda k: fund_monthly[k][2])
                rest = sum(fund_monthly[k][2] for k in range(len(fund_monthly)) if k != j) or 1.0
                for k in range(len(fund_monthly)):
                    fund_monthly[k][2] = total * (
                        big if k == j else fund_monthly[k][2] / rest * (1 - big)
                    )

            # ── Simulate the back-dated contribution history per fund ───────────────
            entries = []  # [fund_id, goal_obj, monthly_amount, rows]
            total_current_value = 0.0
            for fid, goal, monthly in fund_monthly:
                if monthly <= 0:
                    continue
                rows = _make_sip_buys(rng, series, fid, monthly, invest_years)
                net_units = sum(u if t == "buy" else -u for _, t, u, _, _ in rows)
                total_current_value += net_units * series[fid][1][-1]
                entries.append([fid, goal, monthly, rows])

            # Age-appropriate ceiling: if the simulated history outruns what someone this
            # age would plausibly have accumulated, scale BOTH the historical buys and the
            # recorded SIP down together — the two must never drift apart, even here.
            age_cap = _portfolio_cap(age)
            scale = 1.0
            if age_cap is not None and total_current_value > age_cap:
                scale = age_cap * rng.uniform(0.6, 0.95) / total_current_value
            if scale != 1.0:
                for entry in entries:
                    entry[2] *= scale
                    entry[3] = [
                        (d, t, round(u * scale, 4), n, round(u * scale * n, 2))
                        for d, t, u, n, _ in entry[3]
                    ]

            # ── Write transactions + goal tags ───────────────────────────────────────
            for fid, goal, monthly, rows in entries:
                for nav_date, txn_type, units, nav, amount in rows:
                    if units <= 0:
                        continue
                    session.add(Transaction(
                        client=client, fund_id=fid, date=nav_date, type=txn_type,
                        units=units, nav=nav, amount=amount,
                    ))
                    stats["transactions"] += 1

                session.add(GoalHolding(client=client, fund_id=fid, goal=goal))
                stats["goal_holdings"] += 1

            # ── Write the SIP schedule — the exact monthly figure the transactions
            # above were built from, shared start date per goal ──────────────────────
            by_goal: dict = defaultdict(list)  # goal_obj -> [(fund_id, monthly_amount)]
            for fid, goal, monthly, _ in entries:
                by_goal[goal].append((fid, monthly))
            for goal, funds in by_goal.items():
                stepup = rng.choice([0, 0, 0, 0.05, 0.10])
                active = rng.random() < 0.9
                start = BASE_DATE - timedelta(days=rng.randrange(30, max(31, int(invest_years * 365))))
                for fid, monthly in funds:
                    amount = round(monthly, -2)
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
            if needs_attention:
                stats["needs_attention"] += 1

        session.commit()
    finally:
        if own_session:
            session.close()

    return dict(stats)


if __name__ == "__main__":
    print(generate())
