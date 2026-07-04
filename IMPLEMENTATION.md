# AdvisorOS — Implementation Guide

Concrete build plan for the hackathon cut (see `HACKATHON.md` for scope). **The app tracks mutual funds and nothing else** — no stocks, FDs, cash instruments, insurance, or loans. One instrument type keeps the data model tiny and the simulation honest. Every fund carries one of **fourteen category tags**, and those categories — not individual funds — are what the engine simulates.

Stack:

- **Backend + compute:** FastAPI + NumPy (GPU story: NumPy now, swap to CuPy for the GPU run — see [GPU swap](#gpu-swap))
- **Frontend:** Svelte (SvelteKit)
- **DB:** PostgreSQL
- **LLM:** any OpenAI-compatible tool-calling endpoint (e.g. an NVIDIA NIM model) — the tool schemas are provider-agnostic

---

## 0. The fourteen categories

Every fund maps to exactly one of these tags. The tags *are* the "asset classes" the engine simulates — the covariance matrix is built across them (14×14), not across individual funds. Defined once here; referenced everywhere.

```
high_risk_equity      mid_risk_equity       low_risk_equity       international_equity
cash_equivalent       good_debt             bad_debt              gold
silver                aggressive_hybrid     balanced_advantage    conservative_hybrid
multi_asset           other
```

See `HACKATHON.md` § "The one thing we track" for how AMFI sub-categories collapse into these buckets. In code, pin the order once and reuse it as the canonical index for every category vector and for the rows/cols of `Σ`:

```python
# engine/categories.py
CATEGORIES = [
    "high_risk_equity", "mid_risk_equity", "low_risk_equity", "international_equity",
    "cash_equivalent", "good_debt", "bad_debt", "gold",
    "silver", "aggressive_hybrid", "balanced_advantage", "conservative_hybrid",
    "multi_asset", "other",
]
CAT_INDEX = {c: i for i, c in enumerate(CATEGORIES)}
N_CATEGORIES = len(CATEGORIES)   # 14
```

**Two levels of granularity, on purpose:**
- **Funds** carry real NAVs → used for *valuation*, *concentration*, *portfolio-value-over-time*.
- **Categories** carry the return dynamics (`mu`, `sigma`, `Σ`) → the engine simulates the 14-vector, and each fund inherits its category's dynamics. `Σ` stays 14×14 regardless of how many funds exist.

---

## 1. Architecture at a glance

```
Svelte (SvelteKit)                    FastAPI                         Postgres
────────────────────         ──────────────────────────        ──────────────
Copilot chat box     ──POST /copilot──▶ LLM loop ──calls──▶ tools ──reads/writes──▶  clients, goals,
Book Radar table     ──GET  /book/radar────────────────────────────┐               funds, nav_history,
Client Detail page   ──GET  /clients/{id} ─────────────────────────┤               transactions,
  ├ Profile/Goals    ──POST /clients/{id}/transactions:parse ──▶ LLM┤               sip_schedule,
  │                                                                 │               goal_holdings,
  │                                                                 │               assumptions (+Σ),
  ├ Transactions(NL) ──POST /clients/{id}/transactions ─────────────┤               + derived:
  └ Holdings         ──GET  /clients/{id}/holdings ─────────────────┤               baseline_runs,
What-If card         ──POST /clients/{id}/whatif ──▶ MC engine ──────┤               radar_output
Stress card          ──POST /book/stress ─────────▶ MC engine ───────┘
                                                     (NumPy/CuPy)
```

Three layers, matching `HACKATHON.md`:
1. **Data layer** — Postgres tables (below). Transactions are the source of truth; holdings are derived.
2. **Analytics layer** — one NumPy Monte Carlo engine over the 14-category vector + a few deterministic checks.
3. **AI layer** — FastAPI endpoints wrapped as LLM tools; the Copilot loop calls them.

---

## 2. Repo structure

```
advisor/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                 # FastAPI app + router registration
│  │  ├─ db.py                   # SQLAlchemy/psycopg pool, session dep
│  │  ├─ models.py               # ORM tables (or plain SQL in queries.py)
│  │  ├─ schemas.py              # Pydantic request/response models
│  │  ├─ config.py               # env: DB url, LLM url/key, N_PATHS, SEED
│  │  ├─ engine/
│  │  │  ├─ categories.py        # CATEGORIES list + CAT_INDEX (section 0)
│  │  │  ├─ montecarlo.py        # the core simulation (NumPy)
│  │  │  ├─ market.py            # mu/sigma/Σ derived from nav_history + Cholesky
│  │  │  ├─ pipelines.py         # goal prob, VaR/CVaR, suitability, concentration
│  │  │  └─ cache.py             # write baseline_runs / read latest_baseline for what-if
│  │  ├─ tools/
│  │  │  ├─ registry.py          # tool JSON schemas + dispatch table
│  │  │  └─ impl.py              # query_book / get_client_brief / run_whatif /
│  │  │                          #   stress_book / rank_book / add_transactions
│  │  ├─ llm/
│  │  │  └─ copilot.py           # OpenAI-compatible chat loop with tool calling
│  │  └─ api/
│  │     ├─ copilot.py           # POST /copilot
│  │     ├─ book.py              # GET /book/radar, POST /book/stress
│  │     └─ clients.py           # client detail, holdings, transactions, whatif
│  ├─ scripts/
│  │  ├─ seed.py                 # ~300–500 synthetic clients + funds + NAV + txns
│  │  ├─ load_navs.py            # ingest AMFI daily NAV dump into nav_history
│  │  └─ nightly.py              # derive assumptions + run whole book, fill caches
│  └─ requirements.txt
├─ frontend/                     # SvelteKit app (section 8)
└─ db/
   └─ schema.sql                 # DDL
```

---

## 3. Postgres schema

Mutual funds only. Nine concerns, most of them tiny. Transactions are the source of truth; holdings and portfolio-value-over-time are **derived** (views/queries), never materialized. `db/schema.sql`:

```sql
-- ── Input tables ─────────────────────────────────────────
create table clients (
  id            serial primary key,
  name          text not null,
  age           int,
  risk_profile  text check (risk_profile in ('conservative','balanced','aggressive'))
);

create table goals (
  id            serial primary key,
  client_id     int references clients(id) on delete cascade,
  name          text,
  target_amount numeric,                     -- ₹
  target_date   date
);
-- Which funds fund which goal is NOT stored here. A goal is funded by several
-- funds (and by both current holdings and future SIPs), so the mapping lives in
-- goal_holdings below, keyed by (client_id, fund_id). Derive a goal's category
-- set by joining goal_holdings -> funds.category.

-- The only instrument type. `category` is the bridge from a real fund to the
-- engine's return dynamics. scheme_code is the AMFI code (optional).
create table funds (
  id            serial primary key,
  name          text not null,               -- 'Parag Parikh Flexi Cap', 'SBI Gold Fund'
  amc           text,                         -- fund house
  scheme_code   text,                         -- AMFI scheme code, nullable
  category      text not null check (category in (
    'high_risk_equity','mid_risk_equity','low_risk_equity','international_equity',
    'cash_equivalent','good_debt','bad_debt','gold','silver','aggressive_hybrid',
    'balanced_advantage','conservative_hybrid','multi_asset','other'))
);

-- Daily NAV series per fund. Powers valuation, portfolio-over-time, AND the
-- derived market model (mu/sigma/Σ). Seed from AMFI's daily NAV dump.
create table nav_history (
  fund_id       int references funds(id) on delete cascade,
  date          date not null,
  nav           numeric not null,
  primary key (fund_id, date)
);
create index on nav_history (fund_id, date desc);

-- Buy/redeem ledger — the SOURCE OF TRUTH for holdings. Entered via natural
-- language (add_transactions), confirmed by the advisor before commit.
-- buys add units, redeems subtract. amount = units * nav.
create table transactions (
  id            bigserial primary key,
  client_id     int references clients(id) on delete cascade,
  fund_id       int references funds(id) on delete cascade,
  date          date not null,
  type          text check (type in ('buy','redeem')),
  units         numeric not null,
  nav           numeric not null,            -- NAV at execution
  amount        numeric not null,            -- units * nav
  created_at    timestamptz default now()
);
create index on transactions (client_id, fund_id, date);

-- Derived holdings per (client, fund): net units × latest NAV. Compute as a
-- view; don't materialize. Roll up by funds.category → the 14-vector the engine reads.
create view latest_holdings as
  select
    t.client_id,
    t.fund_id,
    f.category,
    sum(case when t.type='buy' then t.units else -t.units end)              as units,
    sum(case when t.type='buy' then t.units else -t.units end)
      * (select nh.nav from nav_history nh
         where nh.fund_id = t.fund_id order by nh.date desc limit 1)        as value
  from transactions t
  join funds f on f.id = t.fund_id
  group by t.client_id, t.fund_id, f.category
  having sum(case when t.type='buy' then t.units else -t.units end) > 0;

-- Forward-looking recurring contributions — the SIP schedule. One row per
-- (client, fund) plan. This is the FUTURE counterpart to `transactions` (the
-- realized past ledger). The goal a SIP funds is inherited from goal_holdings
-- via (client_id, fund_id) — not stored again here. The engine rolls these up
-- by funds.category into the monthly-contribution vector.
create table sip_schedule (
  id             serial primary key,
  client_id      int references clients(id) on delete cascade,
  fund_id        int references funds(id) on delete cascade,
  monthly_amount numeric not null,           -- ₹/month into this fund
  stepup_rate    numeric default 0,          -- optional annual step-up, e.g. 0.10 = +10%/yr
  start_date     date,                        -- nullable; defaults to projection start
  active         boolean default true
);
create index on sip_schedule (client_id, fund_id);

-- Tags each of a client's fund holdings to the goal it funds. A goal is funded
-- by several funds; both the current holding value AND future SIPs into that
-- fund count toward the mapped goal. One goal per (client, fund) — split a fund
-- across goals only if you really need to (then drop the PK for a composite one).
create table goal_holdings (
  client_id     int references clients(id) on delete cascade,
  fund_id       int references funds(id) on delete cascade,
  goal_id       int references goals(id) on delete cascade,
  primary key (client_id, fund_id)
);
create index on goal_holdings (goal_id);

-- Market model, one row per CATEGORY (shared across all clients). mu/sigma are
-- derived nightly from nav_history (see section 4); keep a hardcoded fallback.
create table assumptions (
  category      text primary key,            -- one of the 14 tags
  mu            numeric,                      -- annual expected return
  sigma         numeric                       -- annual volatility
);

-- The 14×14 category covariance matrix, stored as symmetric pairs.
-- Combine into a dense Σ at load time, then Cholesky-factor once.
create table covariances (
  cat_a         text,
  cat_b         text,
  cov           numeric,
  primary key (cat_a, cat_b)
);

-- ── Derived / output tables (filled by nightly.py) ───────
-- The overnight sim cache. ONE row per client per run (dated), append-only so
-- history accumulates. run_whatif reads the latest row so only the delta re-runs.
create table baseline_runs (
  id            bigserial primary key,
  client_id     int references clients(id) on delete cascade,
  as_of_date    date not null,
  seed          int,
  n_paths       int,
  goals         jsonb,          -- [{goal_id, success_prob, terminal_pcts:{p5,p50,p90}, shortfall}]
  var_95        numeric,
  cvar_95       numeric,
  max_drawdown  numeric,
  suitability_mismatch numeric,
  risk_score    numeric,
  created_at    timestamptz default now(),
  unique (client_id, as_of_date)
);
create index on baseline_runs (client_id, as_of_date desc);

-- Fast "latest per client" lookup for the what-if hot path.
create view latest_baseline as
  select distinct on (client_id) *
  from baseline_runs
  order by client_id, as_of_date desc;

create table radar_output (                  -- book-level ranked list
  client_id     int primary key references clients(id) on delete cascade,
  suitability_mismatch numeric,              -- simulated downside - tolerable
  tolerable_dd  numeric,
  simulated_dd  numeric,
  flags         jsonb,                       -- ['off_track','concentrated_fund','concentrated_category', ...]
  updated_at    timestamptz default now()
);

-- ── Copilot chat persistence ─────────────────────────────
create table copilot_messages (
  id            bigserial primary key,
  session_id    uuid not null,               -- groups messages into one conversation
  client_id     int references clients(id) on delete cascade,  -- nullable: book-wide
  role          text check (role in ('user','assistant','tool')),
  content       text,
  tool_calls    jsonb,                       -- assistant turn: [{name, arguments}]; else null
  created_at    timestamptz default now()
);
create index on copilot_messages (session_id, created_at);
```

**Portfolio-value-over-time** is derived, not stored: reconstruct any date's value from `transactions` (units held on that date) × `nav_history` (NAV that date). Feeds the Holdings chart.

---

## 4. The market model — derived from NAV history

Because every fund has a daily NAV series, the assumptions layer is **computed, not guessed** — a real data story. `engine/market.py`:

```python
import numpy as np
from .categories import CATEGORIES, N_CATEGORIES

def derive_assumptions(nav_by_fund, fund_category):
    """
    nav_by_fund:   {fund_id: pd.Series of daily NAV indexed by date}
    fund_category: {fund_id: category tag}
    Returns (mu[14], sigma[14], Sigma[14x14]) annualized.
    """
    # 1. Per-category monthly log-return series: equal-weight member funds.
    cat_returns = {c: [] for c in CATEGORIES}
    for fund_id, nav in nav_by_fund.items():
        monthly = nav.resample("M").last()
        r = np.diff(np.log(monthly.values))          # monthly log-returns
        cat_returns[fund_category[fund_id]].append(r)

    # Equal-weight funds within each category, align lengths.
    series = []
    for c in CATEGORIES:
        rs = cat_returns[c]
        series.append(np.mean(np.vstack(_align(rs)), axis=0) if rs else np.zeros(1))
    R = np.vstack(_align(series))                    # (14, T) category return matrix

    # 2. Annualize: mean*12, std*sqrt(12), cov*12.
    mu    = R.mean(axis=1) * 12
    sigma = R.std(axis=1)  * np.sqrt(12)
    Sigma = np.cov(R)      * 12                       # 14×14
    return mu, sigma, Sigma

def cholesky(Sigma):
    return np.linalg.cholesky(Sigma)                 # L, reused across all sims
```

- Recompute nightly alongside the baseline cache and persist to `assumptions` + `covariances` (or cache in memory for the demo).
- **Fallback:** ship a hardcoded assumptions table + correlation matrix so the engine runs even with thin NAV history. Sensible India-market numbers per category (equity ~12–16%/18–25%, good_debt ~7%/4%, cash_equivalent ~5%/1%, gold ~8%/15%; equity–gold correlation slightly negative).
- **Split the mixed AMFI tags at load time** (`load_navs.py`): "Index Funds" and "Other ETFs" mix equity and debt — assign by the fund's name/underlying (equity → `low_risk_equity`, bond → `good_debt`), not by the AMFI string alone.

---

## 5. The Monte Carlo engine (NumPy)

The one piece worth getting right. Pure function of its inputs — same inputs + seed → same numbers. It only ever sees **14-category vectors**. `engine/montecarlo.py`:

```python
import numpy as np

def simulate(
    holdings: np.ndarray,        # (14,)  current ₹ per category (rolled up from funds)
    mu: np.ndarray,              # (14,)  annual expected return per category
    L: np.ndarray,               # (14,14) Cholesky factor of the category covariance Σ
    monthly_sip: np.ndarray,     # (14,)  contribution per category per month
    horizon_months: int,
    n_paths: int = 50_000,
    steps_per_year: int = 12,
    seed: int = 42,
    stepup_rate: float = 0.0,    # optional annual SIP step-up
    shock: dict | None = None,   # e.g. {"month": 0, "deltas": {cat_idx: -0.20}}
) -> np.ndarray:
    """Return terminal portfolio value per path -> shape (n_paths,)."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / steps_per_year
    n = holdings.shape[0]                                # 14

    drift = (mu - 0.5 * np.diag(L @ L.T)) * dt           # per-step drift
    value = np.tile(holdings, (n_paths, 1)).astype(float)  # (paths, 14)
    sip = monthly_sip.copy()

    for t in range(horizon_months):
        z = rng.standard_normal((n_paths, n))
        correlated = z @ L.T * np.sqrt(dt)               # correlated shocks across categories
        value *= np.exp(drift + correlated)              # lognormal step
        if shock and shock.get("month") == t:            # inject a market shock
            for cat_idx, delta in shock["deltas"].items():
                value[:, cat_idx] *= (1 + delta)
        value += sip                                     # inject SIP each month
        if stepup_rate and (t + 1) % steps_per_year == 0:
            sip *= (1 + stepup_rate)                      # annual step-up

    return value.sum(axis=1)                             # (paths,) terminal totals
```

Notes:
- **Per-category inputs** — `holdings` and `monthly_sip` are 14-vectors: when loading a client, roll their funds up by `category` (from `latest_holdings`) and sum `value` → holdings; roll their `sip_schedule` rows up by each fund's `category` → contributions. The engine never sees individual funds. If step-up rates differ across funds within a category, pass `stepup_rate` as a contribution-weighted per-category value (or make it a 14-vector) rather than one scalar.
- **Per-goal outputs** — a goal's success/shortfall simulates only the sub-portfolio of funds tagged to it: join `goal_holdings` → `funds.category` to get the goal's category subset, and include just those funds' holdings + SIPs. A goal funded by several funds across categories rolls up correctly.
- **Vectorized across all paths** — the loop is over *months* (≤ a few hundred), never over paths. That's what keeps it GPU-friendly.
- `shock` is how both single-client what-if and book-wide `stress_book` inject a market move. `shock["deltas"]` keys are **category indices** (`CAT_INDEX[...]`).
- `Σ` captures correlated spillover — a small-cap crash doesn't happen alone; gold rising / good_debt holding is baked into the joint draw.

### Reading outputs off the distribution — `pipelines.py`

```python
def goal_probability(terminals, target):     return float((terminals >= target).mean())

def shortfall(terminals, target):             # expected & worst-case gap (₹) for off-track goals
    gap = np.maximum(target - terminals, 0)
    return {"expected": float(gap.mean()), "worst_p5": float(np.quantile(target - terminals, 0.95))}

def required_sip(client_vec, target, horizon, confidence=0.80, **kw):
    """Bisect monthly SIP until success_probability >= confidence."""
    lo, hi = 0.0, client_vec["monthly_sip"].sum() * 10 + 1e5
    for _ in range(18):
        mid = (lo + hi) / 2
        p = goal_probability(simulate(monthly_sip=_scale(client_vec, mid), target=target, ...), target)
        lo, hi = (mid, hi) if p < confidence else (lo, mid)
    return hi

def var_cvar(terminals, start_value, pct=0.05):
    losses = (start_value - terminals) / start_value
    var  = float(np.quantile(losses, 1 - pct))    # loss not exceeded 95% of the time
    cvar = float(losses[losses >= var].mean())     # mean of the worst tail
    return var, cvar

def percentiles(terminals):                        # {p5,p50,p90}
    p5, p50, p90 = np.quantile(terminals, [0.05, 0.5, 0.90])
    return {"p5": float(p5), "p50": float(p50), "p90": float(p90)}
```

### Suitability, concentration + stress (book-wide)

```python
from .categories import CAT_INDEX

TOLERABLE_DD = {"conservative": -0.10, "balanced": -0.20, "aggressive": -0.35}

def suitability_mismatch(simulated_dd, risk_profile):
    return simulated_dd - TOLERABLE_DD[risk_profile]   # >0 => over-exposed

def concentration_flags(fund_values, category_values, total):
    """Single-fund and single-category over-exposure."""
    flags = []
    if fund_values and max(fund_values.values()) / total > 0.25:      flags.append("concentrated_fund")
    if category_values and max(category_values.values()) / total > 0.40: flags.append("concentrated_category")
    return flags

def stress_book(clients, shock, deterministic=True):
    """Apply one shock across all clients; return ranked breach list.
       shock: {category_tag: delta, ..., 'horizon_months': int}."""
    out = []
    for c in clients:
        if deterministic:                              # instant arithmetic version
            loss = sum(c.category_value.get(cat, 0) / c.total * delta
                       for cat, delta in shock.items() if cat in CAT_INDEX)
        else:                                          # MC version (correlated spillover)
            deltas = {CAT_INDEX[cat]: d for cat, d in shock.items() if cat in CAT_INDEX}
            terminals = simulate(..., shock={"month": 0, "deltas": deltas})
            loss = (c.total - np.quantile(terminals, 0.05)) / c.total * -1
        tol = TOLERABLE_DD[c.risk_profile]
        if loss < tol:                                 # breach
            out.append({"client_id": c.id, "loss": loss, "severity": tol - loss})
    return sorted(out, key=lambda x: x["severity"], reverse=True)
```

- **Deterministic shock** — `loss ≈ Σ (category_weight × shock)`; a vectorized reduction, not Monte Carlo. Use for the literal "small-cap drops exactly 20%" question.
- **Monte Carlo shock** — adds what the arithmetic can't: probability of the event, correlated spillover via `Σ`, and the goal cascade (shock → `success_probability`).

### <a name="gpu-swap"></a>GPU swap

Write everything against a module alias so the GPU switch is one line:

```python
# engine/backend.py
try:
    import cupy as xp        # GPU
    GPU = True
except ImportError:
    import numpy as xp       # CPU fallback — demo still runs
    GPU = False
```

Replace `np` with `xp` in `montecarlo.py`. `cupy.random`, `cupy.linalg.cholesky`, `@`, and `cupy.quantile` are all drop-in. Keep a **timer** around `simulate()` and report GPU vs CPU wall-time in the API response — that number is part of the pitch. `paths × steps × categories × clients` reaches tens of millions of path-years per run; each path is independent → embarrassingly parallel.

---

## 6. FastAPI endpoints

```
GET  /clients                            -> list (id, name, risk_profile, flags)
GET  /clients/{id}                       -> profile + goals + latest baseline
GET  /clients/{id}/holdings              -> per-fund units×NAV, category roll-up, concentration
GET  /clients/{id}/brief                 -> one-page brief payload (from caches + goals)
POST /clients/{id}/transactions:parse    -> {text} -> structured txn rows for advisor to confirm (LLM)
POST /clients/{id}/transactions          -> commit confirmed rows to the ledger
POST /clients/{id}/whatif                -> {changes} -> before/after prob + tail  (live MC)
GET  /book/radar                         -> ranked suitability list (from radar_output)
POST /book/stress                        -> {shock, filters} -> breach count + ranked list
POST /copilot                            -> {message, history} -> LLM loop, returns answer + tool results
```

- `/whatif` and `/book/stress` run the engine live (~1s target). Everything else reads precomputed caches / derived views → instant.
- `/transactions:parse` and `/transactions` are the two halves of the NL data-entry flow: parse → advisor confirms/edits → commit. **A misparse must be caught before it corrupts holdings**, so parse never writes.
- Pydantic `schemas.py` mirrors the tool JSON schemas in section 7 so validation is shared.

`scripts/nightly.py` derives the market model from `nav_history`, runs the whole book through `simulate()` once, **appends** a dated row per client to `baseline_runs`, and refreshes `radar_output`. For the hackathon this can be a button ("Run book analysis") instead of a cron. Each run adds a new dated snapshot, so history accumulates — seed a couple of back-dated runs so "since last time" has something to show on stage.

---

## 7. LLM tool-calling layer

The Copilot is scoped to a **fixed six-tool set** (structured, not open chat). `tools/registry.py` holds JSON schemas; `tools/impl.py` maps each to a Python function that hits the same logic as the REST endpoints.

```python
TOOLS = [
  {"type":"function","function":{
    "name":"query_book",
    "description":"Find clients matching criteria (off-track, over-exposed, by risk profile, by category exposure).",
    "parameters":{"type":"object","properties":{
      "off_track":{"type":"boolean"},
      "risk_profile":{"type":"string","enum":["conservative","balanced","aggressive"]},
      "over_exposed":{"type":"boolean"},
      "category":{"type":"string","description":"exposure to one of the 14 category tags"}
    }}}},
  {"type":"function","function":{
    "name":"get_client_brief",
    "description":"Pull the latest per-client analysis (goal probabilities, risk, suitability).",
    "parameters":{"type":"object","properties":{"client_id":{"type":"integer"}},"required":["client_id"]}}},
  {"type":"function","function":{
    "name":"run_whatif",
    "description":"Re-simulate one client with a change and return before/after probability + tail risk.",
    "parameters":{"type":"object","properties":{
      "client_id":{"type":"integer"},
      "sip_delta":{"type":"number","description":"change monthly SIP (₹)"},
      "reallocate":{"type":"object","description":"shift target weight between categories, e.g. {\"from\":\"high_risk_equity\",\"to\":\"good_debt\",\"pct\":0.10}"},
      "reduce_concentration":{"type":"object","description":"cap a single fund / de-risk a position"},
      "lump_sum":{"type":"number","description":"one-time injection (+) or withdrawal (-)"},
      "horizon_shift":{"type":"integer","description":"move a goal's target date by N months"},
      "return_shock":{"type":"object","description":"stress assumptions, e.g. {\"high_risk_equity\":-0.02}"}
    },"required":["client_id"]}}},
  {"type":"function","function":{
    "name":"stress_book",
    "description":"Apply one market shock across the whole book; return who breaches tolerance.",
    "parameters":{"type":"object","properties":{
      "shock":{"type":"object","description":"per-category delta + horizon_months, e.g. {\"high_risk_equity\":-0.20,\"horizon_months\":3}"},
      "filters":{"type":"object"}
    },"required":["shock"]}}},
  {"type":"function","function":{
    "name":"rank_book",
    "description":"The suitability-mismatch call list across the whole book.",
    "parameters":{"type":"object","properties":{"limit":{"type":"integer"}}}}},
  {"type":"function","function":{
    "name":"add_transactions",
    "description":"Parse plain-English fund activity into structured transaction rows for the advisor to confirm before commit.",
    "parameters":{"type":"object","properties":{
      "client_id":{"type":"integer"},
      "text":{"type":"string","description":"e.g. '₹50k into HDFC Small Cap, ₹20k into an SBI gold fund last month'"}
    },"required":["client_id","text"]}}},
]
```

Copilot loop (`llm/copilot.py`), OpenAI-compatible so it works against an NVIDIA NIM endpoint:

```python
def copilot(message, history):
    msgs = history + [{"role":"user","content":message}]
    while True:
        resp = client.chat.completions.create(model=MODEL, messages=msgs, tools=TOOLS)
        choice = resp.choices[0].message
        if not choice.tool_calls:
            return choice.content                     # final natural-language answer
        for call in choice.tool_calls:                # execute each tool, feed result back
            result = DISPATCH[call.function.name](**json.loads(call.function.arguments))
            msgs.append({"role":"tool","tool_call_id":call.id,"content":json.dumps(result)})
```

The advisor's sentence *"if small-cap fell 20% over 3 months, who couldn't stomach it?"* → the model emits `stress_book({shock:{high_risk_equity:-0.20,horizon_months:3}})` → the loop runs it → the model narrates the ranked result. Chaining works too: *"prep me for my 3pm with Rahul"* → `query_book` → `get_client_brief`.

---

## 8. Seeding data

Two ingest paths. `scripts/load_navs.py` seeds real funds + NAV history from AMFI's daily NAV dump (split the mixed Index/ETF tags by name — see section 4). `scripts/seed.py` — generate ~300–500 synthetic clients so the book-level story is real:

- random age, name, risk profile (weighted toward balanced)
- 1–3 goals each (retirement, education, house) with realistic targets
- a **fund mix per profile** built from the seeded funds (conservative → good_debt / cash_equivalent / conservative_hybrid heavy; aggressive → high/mid_risk_equity heavy), expressed as `transactions` (buy rows at historical NAVs) so holdings derive correctly
- `goal_holdings` rows tagging each client's fund holdings to a goal, and a per-fund `sip_schedule` (monthly amount + optional step-up) — a single goal may be funded by several funds via both holdings and SIPs
- **deliberately plant edge cases**: a few conservative clients holding heavy `high_risk_equity` (so the radar + stress test light up on stage), and a couple with a single fund > 25% of portfolio (concentration flag)
- if NAV history is thin, fill the hardcoded `assumptions` + `covariances` fallback (section 4)

---

## 9. Svelte frontend (SvelteKit)

The interactive features collapse into one **Copilot Workspace** rather than living on separate pages — that keeps the "spoken to, not clicked through" pitch intact.

```
frontend/src/
├─ lib/
│  ├─ api.ts                     # fetch wrappers for the endpoints
│  └─ components/
│     ├─ Copilot.svelte          # chat box -> POST /copilot; renders answers + tool-call trace
│     ├─ WhatIfCard.svelte       # levers (SIP, reallocate, reduce concentration, lump sum,
│     │                          #   horizon shift, return shock) -> before/after diff
│     ├─ StressCard.svelte       # preset scenario buttons -> POST /book/stress -> ranked fallout
│     ├─ RadarTable.svelte       # ranked suitability list, sortable, summary tiles
│     ├─ TxnEntry.svelte         # NL box -> :parse -> confirm/edit grid -> commit
│     ├─ Holdings.svelte         # per-fund value, category allocation, concentration, value-over-time
│     ├─ Brief.svelte            # one-page client brief (deferred; brief-lite from cache first)
│     └─ GpuBadge.svelte         # shows GPU vs CPU timing from the response
└─ routes/
   ├─ +page.svelte               # Book Risk Radar (landing dashboard)
   ├─ copilot/+page.svelte       # Copilot Workspace (what-if + stress render inline as cards)
   └─ clients/[id]/+page.svelte  # Client Detail: Profile/Goals · Transactions(NL) · Holdings tabs
```

Screen priorities (see `HACKATHON.md` § "The screens"):
- **Screen A — Copilot Workspace (centerpiece):** chat thread with preset prompt chips, a **visible tool-call trace** (`→ run_whatif on GPU`), a **persistent GPU-vs-CPU timer**, and what-if / stress rendering inline as interactive cards. Both what-if and stress are the *same surface* — a message in, a rich card back.
- **Screen B — Book Risk Radar (differentiator):** landing dashboard. Summary tiles (off-track count, over-exposed count, "worst year worse than −25%" count, off-track goals) above the ranked "who do I call first, and why" list. Rows link into the client.
- **Client Detail (data entry + holdings):** Profile/goals editor (targets), a **fund→goal tagging** control (which held funds fund which goal, i.e. `goal_holdings`), and a per-fund **SIP schedule editor** (monthly amount + step-up) · **Transactions NL entry** (the source of truth — parse → confirm → commit, with an editable ledger below) · derived Holdings (per-fund value, category allocation, concentration flags, portfolio-value-over-time line, read-only).
- **Screen C — Meeting Brief (deferred):** build after the core loop; until then the Copilot renders a "brief-lite" (goal probabilities + suitability flag from the baseline cache).

Keep charts simple: probability as a labeled progress bar, terminal-value percentiles as a P5/P50/P90 band, allocation as a simple category donut. No charting library needed to land the point.

---

## 10. Build order (thin vertical slices)

Ship top-down; each step is demoable on its own.

1. **DB + NAV load + seed** — schema, `load_navs.py`, `seed.py`; confirm ~400 clients with funds/transactions and planted edge cases.
2. **Market model** — derive `mu/sigma/Σ` from `nav_history` (with hardcoded fallback), Cholesky once.
3. **MC engine + pipelines** — `simulate()` over the 14-vector returning goal prob + VaR/CVaR for one client, verified against a hand-checked case.
4. **Nightly fill** — run the book, append dated rows to `baseline_runs` + refresh `radar_output`.
5. **Read APIs + Radar/Holdings UI** — the deliverables render from cache + derived views.
6. **What-if** — live single-client re-sim + before/after card. *(hero demo #1)*
7. **stress_book** — book-wide shock + StressCard presets. *(hero demo #2)*
8. **Transactions NL entry** — `add_transactions` parse → confirm → commit (reuses the Gen-AI pattern for data entry).
9. **Copilot** — wire the six-tool loop so all of the above are reachable by natural language.
10. **GPU swap + timer** — flip NumPy→CuPy, show the speedup badge.
11. **Polish** — deterministic seed for stage, 2–3 rehearsed Copilot prompts.

If time runs short, stop after step 6 — engine + what-if + a rendered radar/holdings is still a complete demo.

---

## 11. Config / env

```
DATABASE_URL=postgres://...
LLM_BASE_URL=...              # OpenAI-compatible endpoint (e.g. NVIDIA NIM)
LLM_API_KEY=...
LLM_MODEL=...
MC_N_PATHS=50000
MC_SEED=42                    # fixed for reproducible stage numbers
MC_STEPS_PER_YEAR=12
MC_CONFIDENCE=0.80            # target success threshold for required_sip
USE_GPU=auto                  # auto|true|false
```
