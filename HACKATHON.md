# AdvisorOS — Hackathon Cut (Mutual-Funds Only)

A GPU-powered early-warning system and meeting co-pilot for financial advisors. This is the trimmed build plan: everything that makes the idea land in a demo, nothing that just widens the surface. **The entire app tracks mutual funds and nothing else** — no stocks, FDs, cash instruments, insurance, or loans. One instrument type keeps the data model tiny and the simulation honest.

> **The pitch in one line:** AdvisorOS watches an advisor's whole book of mutual-fund portfolios with thousands of simulated market futures per client, ranks who needs attention this week, and — live in a meeting — re-runs 50,000 futures in a second to show whether a recommendation actually helps.

---

## What we're actually building

This is a **Gen AI + NVIDIA** hackathon, so the story has two legs: an **LLM agent that reasons and calls tools**, and a **GPU engine those tools run on**. The LLM is the interface; the GPU is the muscle. Ship top-down; stop when time runs out.

1. **The Monte Carlo engine (GPU).** The one technical core. Everything else is a tool the LLM can call. This is what justifies the GPU.
2. **The Advisor Copilot (LLM + tool calling).** Natural language in, tool calls out — the Gen AI centerpiece (see below).
3. **Live What-If (the hero demo).** "Increase Rahul's SIP by ₹5k" → success probability 62% → 81% in ~1 second — driven *by the LLM from plain English*.
4. **Book Risk Radar (the deliverable).** A ranked "who needs attention" list across the whole book, authored by the LLM.

If nothing else works, #1 + #2 + #3 is still a winning demo: an advisor talks to it, it calls the GPU, and the answer comes back as probabilities.

---

## Why it matters (kept short)

Advisors manage hundreds of mutual-fund clients. Two pains: **meeting prep is 20–30 min of manual spreadsheet work per client**, and **problems get noticed too late**. Naive projections ("assume 12%/yr") hide all downside. We replace point estimates with *probabilities and worst-cases*, computed fast enough to cover the whole book and to answer live in a meeting.

The advisor is the only user. No client portal.

---

## The one thing we track: mutual funds by category

Every fund carries a **category tag** describing what its underlying assets actually are. These fourteen categories *are* the "asset classes" the engine simulates — the covariance matrix is built across them, not across individual funds.

The tags collapse AMFI's ~40 SEBI sub-categories into buckets that are distinct in **risk/return behaviour** (what drives `mu`, `sigma`, `Σ`). Crucially, we split what AMFI lumps under "Other Scheme": gold, silver, equity-index, and international all get their own tag instead of a single `other` dumping ground.

| Category | Maps from (AMFI scheme sub-categories) | Character |
|---|---|---|
| `high_risk_equity` | Small Cap, Sectoral/Thematic | highest return, fattest tails |
| `mid_risk_equity` | Mid Cap, Large & Mid Cap, Multi Cap, Flexi Cap, Value, Focused, Contra, ELSS, Dividend Yield | high return, high vol — the diversified-active-equity bulk |
| `low_risk_equity` | Large Cap, **equity** Index Funds & ETFs (Nifty/Sensex/large-cap) | equity beta, calmer |
| `international_equity` | FoF Overseas, US/Nasdaq/China/global funds | equity beta + currency; decoupled from Indian market |
| `cash_equivalent` | Liquid, Overnight, Money Market, Ultra Short Duration, Arbitrage | near-zero vol; parking / market-neutral |
| `good_debt` | Gilt (incl. 10-yr constant), Corporate Bond, Banking & PSU, Short/Low/Medium/Long/Med-to-Long Duration, Dynamic Bond, Floater, **debt** target-maturity index (G-Sec/SDL/CRISIL-IBX) | low vol, mild return, some duration risk |
| `bad_debt` | Credit Risk, low-rated | debt-like until it isn't; tail risk |
| `gold` | Gold ETF, Gold FoF | diversifier; often rises when equity falls |
| `silver` | Silver ETF / FoF (AMFI files these under "Other ETFs") | commodity diversifier; more volatile than gold |
| `aggressive_hybrid` | Aggressive Hybrid, Equity Savings, Balanced Hybrid | equity-tilted, dampened swings |
| `balanced_advantage` | Dynamic Asset Allocation / Balanced Advantage | dynamically hedged equity |
| `conservative_hybrid` | Conservative Hybrid | debt-tilted, small equity kicker |
| `multi_asset` | Multi Asset Allocation, multi-asset FoF | pre-blended equity + debt + gold |
| `other` | Solution-Oriented (Retirement / Children's), multi-sector domestic FoF, legacy/unmapped | true catch-all — kept small |

**Two levels of granularity, on purpose:**
- **Funds** carry real NAVs → used for *valuation*, *concentration*, and *portfolio-value-over-time*.
- **Categories** carry the return dynamics (`mu`, `sigma`, `Σ`) → the engine simulates the category vector, and each fund inherits its category's dynamics. This keeps `Σ` a small **14×14** matrix regardless of how many funds exist.

**One nuance worth knowing:** AMFI's "Index Funds" and "Other ETFs" tags mix equity and debt — a Nifty index fund and a G-Sec target-maturity index fund share the same AMFI label. So those two tags are split by the fund's *name/underlying* (equity → `low_risk_equity`, bond → `good_debt`), not by the AMFI category string alone.

---

## The technical core: one Monte Carlo engine

Written once, reused for everything. This is the whole GPU story — keep it focused.

**Per client, per run:**
1. Build a category covariance matrix `Σ` across the fourteen fund categories; take its Cholesky factor `L`.
2. Draw a large block of standard normals on the GPU: `(paths, steps, categories)` — e.g. 50,000 × 12 × 14.
3. Correlate (`z @ Lᵀ`), apply drift + volatility (lognormal step), compound forward, inject SIP contributions each step → terminal value per path.
4. Read outputs off the distribution (see the full output list below).

### Inputs

Two groups: **client state** (who we're simulating) and **market model** (the world they invest in). The engine is a pure function of these — same inputs + same seed → same numbers.

**Client state**
- `holdings` — current value per **category**, rolled up from the client's funds (each fund valued at its latest NAV × units held). This is the starting portfolio.
- `contributions` — the SIP schedule: a monthly amount **per fund** (₹/month), optionally with a step-up rate (e.g. +10%/yr), rolled up by category for the engine.
- `goals[]` — for each goal: `target_amount` (₹) and `horizon_months` (to target date). Which funds fund a goal is a **per-(client, fund) tag**, not a property of the goal — see the data model below; the engine derives a goal's category set from the funds tagged to it.
- `risk_profile` — conservative / balanced / aggressive → maps to the tolerable-drawdown band used for suitability (−10% / −20% / −35%).

**Market model** (shared across clients; the assumptions layer, **derived from NAV history — see below**)
- `mu[]` — expected annual return per category.
- `sigma[]` — annual volatility per category.
- `Σ` — the category **covariance matrix** (14×14) so categories move together realistically. Factored once to `L` (Cholesky) and reused.

### Assumptions are derived from NAV history (not hardcoded)

Because every fund has a daily NAV series, the market model is computed, not guessed — a real data story:
1. For each category, build a category return series from its member funds' NAVs (equal-weight the funds' monthly log-returns).
2. `mu` = annualized mean, `sigma` = annualized stdev, `Σ` = annualized covariance across the fourteen category series.
3. Recompute nightly alongside the baseline cache. Trivial in NumPy/CuPy: `r = diff(log(nav))`, then `mean`, `std`, `cov`.

Fallback: ship a hardcoded assumptions table so the engine runs even with thin NAV history.

### Tweakable parameters

Two kinds of knobs: **simulation controls** (accuracy vs. speed) and **what-if levers** (the scenario the advisor is testing).

**Simulation controls** — mostly fixed per run; the speed/precision dial.
| Param | Meaning | Typical | Effect |
|---|---|---|---|
| `n_paths` | market futures simulated | 50,000 | ↑ = smoother tails, slower. The main GPU-vs-CPU lever. |
| `steps` | time steps to horizon | 12/yr (monthly) | granularity of compounding + contribution injection |
| `seed` | RNG seed | fixed for demo | reproducible before/after numbers on stage |
| `confidence` | target success threshold | 80% | used to back out "required SIP to hit X% probability" |
| `var_pct` | tail percentile for VaR/CVaR | 5% / 1% | which downside the risk numbers report |

**What-if levers** — the arguments the LLM fills from natural language (`run_whatif`). Each is a *delta on baseline inputs*, then the ensemble re-runs.
- `sip_delta` — change monthly contribution (₹, per fund/category or total).
- `reallocate` — shift target weights between categories (e.g. high-risk-equity → good-debt/gold by X%). Executed as fund switches; modeled at the category level.
- `reduce_concentration` — cap a single fund / de-risk a concentrated position.
- `lump_sum` — one-time injection or withdrawal.
- `horizon_shift` — retire earlier/later (move a goal's target date).
- `return_shock` — stress the assumptions (e.g. equity `mu` −2%) for a downside "what if the market is worse" test.

### Outputs

Returned **per goal** and **per client**, plus a **book-level roll-up**. Everything is a distribution statistic, never a single point estimate.

**Per goal**
- `success_probability` — share of paths reaching `target_amount` by the date.
- `terminal_value` percentiles — median (P50), optimistic (P90), worst-case (P5).
- `shortfall` — expected and worst-case gap to target (₹), for off-track goals.
- `required_sip` — monthly contribution needed to lift probability to `confidence`.

**Per client (portfolio risk)**
- `VaR` — loss not exceeded in 95% of simulated years.
- `CVaR` — average loss in the worst 5% (expected shortfall).
- `simulated_max_drawdown` — the downside used for suitability.
- `suitability_mismatch` = simulated downside − tolerable downside (positive = over-exposed).
- `risk_score` + `concentration` flags (single-fund and single-category over-exposure).

**What-if response** (`run_whatif`) — a *diff*, which is what makes the demo land:
- before → after `success_probability`
- before → after worst-case shortfall (P5)
- before → after `VaR / CVaR`
- one-line "this helps / this doesn't" verdict for the LLM to narrate.

**Book-level roll-up** (`rank_book`) — reduction over all clients' per-client outputs:
- ranked call list by `suitability_mismatch`
- counts, e.g. "how many clients have a simulated worst year worse than −25%"
- off-track goal counts across the book.

To keep the interactive what-if sub-second, **cache each client's baseline distribution overnight** so `run_whatif` only re-runs the changed ensemble, not the whole book.

**Stack:** CuPy for RNG / Cholesky / matmul / percentiles; cuDF to shape holdings; **NumPy fallback so the demo runs GPU-less**. Vectorize across the whole path block — never loop paths in Python.

**Make the GPU visible:** keep a live CPU-vs-GPU timing counter on screen. Measure it, don't estimate it. Fix the random seed so before/after what-if numbers are reproducible on stage.

Why it's GPU-shaped: `paths × steps × categories × clients` reaches tens of millions of simulated path-years per run — seconds on a GPU, an overnight job on CPU. Each path is independent → embarrassingly parallel.

---

## The two Gen AI features (LLM + tool calling)

The GPU pipelines above are exposed to the LLM as **callable tools**. The model never does math itself — it decides *which* tool to call with *what* arguments, then narrates the result. This is the agentic pattern judges will look for.

**Tools the LLM can call** (each is a thin wrapper over the engine / data):
- `query_book(filters)` — return clients matching criteria (off-track, over-exposed, by goal, by risk profile, by category exposure)
- `get_client_brief(client_id)` — pull the latest per-client analysis
- `run_whatif(client_id, changes)` — fire the GPU re-simulation for one client and return before/after probability + tail risk
- `stress_book(shock, filters)` — apply one market shock across the **whole book** and return who breaches their risk tolerance (see below)
- `rank_book()` — the suitability-mismatch call list
- `add_transactions(client_id, text)` — parse a plain-English description of fund activity ("₹50k into HDFC Small Cap, ₹20k into an SBI gold fund last month") into structured transactions the advisor confirms before they commit to the ledger (see the Transactions screen below). Same NL → structured → action pattern, applied to data entry.

### Gen AI Feature 1 — Advisor Copilot (natural-language book queries)
The advisor types or speaks plainly:
> "Show me conservative clients carrying too much high-risk equity." · "Who's off-track on retirement?" · "Prep me for my 3pm with Rahul."

The LLM parses intent, calls the right tool(s), and answers in advisor language — often chaining calls (`query_book` → `get_client_brief`). This replaces menus and filters with a conversation, and shows real **tool orchestration**, not a chatbot bolted on.

### Gen AI Feature 2 — Conversational What-If (LLM drives the GPU)
The hero demo, but spoken in English instead of clicked:
> "What if I bump Rahul's SIP by ₹5k and switch 10% from small-cap into a gilt fund?"

The LLM translates that sentence into a structured scenario, calls `run_whatif`, the **GPU re-runs 50,000 futures in ~1 second**, and the LLM narrates the result: *"Retirement success goes 62% → 81%, and the worst-case shortfall drops from ₹18L to ₹4L."* This is the feature that fuses both hackathon themes in one breath — **LLM reasoning → GPU compute → plain-language answer.**

---

## The screens

Four surfaces, prioritized for the demo. The interactive features (what-if, book stress) collapse into one conversational workspace rather than living on separate pages — that keeps the "spoken to, not clicked through" pitch intact.

### Screen A — Copilot Workspace (demo centerpiece)
The single conversational command center. Everything the advisor *asks* happens here; results render inline as interactive cards.

- **Chat thread** — plain-English input, preset prompt chips (so the demo can't fumble a typo), and a **visible tool-call trace** (`→ run_whatif on GPU`) so judges see orchestration, not just chat.
- **Persistent GPU-vs-CPU timer** on screen.
- **Live What-If** renders as an interactive card in the thread: levers (SIP ↑, reallocate toward debt/gold, reduce single-fund concentration, lump sum, horizon shift, return shock) + a before→after diff (success-probability gauge, P5 shortfall, VaR/CVaR) + the one-line "this helps / this doesn't" verdict. Sub-second re-sim is the thing a CPU tool visibly can't do. Cache each client's baseline overnight so only the delta re-runs.
- **Book Stress** renders as a card too: preset scenario buttons up top, ranked breached-client fallout as the response.

Both what-if and stress are the *same surface* — a message in, a rich card back.

### Screen B — Book Risk Radar (dashboard, the differentiator)
The landing dashboard. Aggregate every client's simulation into one ranked list: **suitability mismatch = simulated downside − tolerable downside** for their risk profile.

- conservative → tolerable ≈ −10%
- balanced → ≈ −20%
- aggressive → ≈ −35%

A conservative client whose fund mix simulates a −28% worst year gets flagged *before* the downturn. That's "the system warned me first." Layout: summary tiles (off-track count, over-exposed count, "worst year worse than −25%" count, off-track goals) above the ranked "who do I call first, and why" list, with filters. Rows link into the client.

### Client Detail (data entry + holdings)
One tabbed screen per client. This is the data the engine runs on.

- **Profile (data entry)** — name, age, risk profile; goals editor (target amount, target date); a **fund→goal tagging** control (which held funds fund which goal → `goal_holdings`); and a per-fund **SIP schedule** editor (monthly amount + optional step-up → `sip_schedule`).
- **Transactions — natural-language entry** *(the source of truth)*. The advisor types fund activity in plain English → `add_transactions` parses it into structured rows (date, type, fund, units, NAV, amount) → the advisor **confirms/edits before commit** (a misparse must be caught before it corrupts holdings) → committed to the ledger. An editable ledger list below handles manual fixes. Reuses the exact Gen-AI pattern, so data entry strengthens the pitch instead of being plumbing.
- **Holdings (display, derived)** — per fund: units held × latest NAV = value; rolled up by category into the `{high_risk_equity … other}` vector the engine reads. Allocation chart (by category and by fund), concentration flags, and a **portfolio-value-over-time** line (reconstructed from the ledger × NAV history). Read-only.

### Screen C — Client Meeting Brief (deferred)
One page per client, generated on demand: current position, goal-by-goal on-track/off-track with probability, top risks/suitability concerns, and 1–2 suggested actions. An AI layer turns the numbers into plain language. **Build after the core loop works** — until then, the Copilot can render a "brief-lite" (goal probabilities + suitability flag straight from the baseline cache).

---

## Book-wide scenarios (`stress_book`) — the "what if the market moves" moment

The single-client what-if is the personal hero moment. Running one scenario across **all clients at once** is the *book* hero moment — and it's the clearest reason the workload wants a GPU, because it's the same simulation multiplied by hundreds of clients.

The advisor asks in plain English; the LLM fills a structured `shock` and calls `stress_book`.

> *"If small-cap equity fell 20% over the next 3 months, how many clients couldn't stomach it?"*
> → `stress_book({ shock: { high_risk_equity: -0.20, horizon_months: 3 }, filters: {} })`
> → *"37 of 500 clients breach their risk tolerance — 31 of them conservative. Here they are, ranked by severity."*

**What it returns**
- count + % of clients breaching their tolerable-drawdown band (−10% / −20% / −35%)
- the **ranked call list** of breached clients (severity = loss − tolerance)
- breakdown by risk profile, and how many goals fall off track as a knock-on

### Two ways to run it — be explicit about which

**A. Deterministic shock (instant, book-wide arithmetic).** Apply the shock directly to holdings: a client's loss ≈ `category_weight × shock`. Compare to their band, count breaches. This is *not* Monte Carlo — it's a vectorized reduction over the book. Honest, fast, and the cleanest thing to show. Use this for the literal "small-cap drops exactly 20%" question.

**B. Monte Carlo shock (where simulation earns its keep).** Three things the deterministic version can't do:
- **Probability of the event** — "in what share of simulated 3-month futures does small-cap fall ≥20%?" Read it off the path distribution instead of assuming it.
- **Correlated spillover** — a 20% equity drop doesn't happen alone. The covariance matrix `Σ` captures gold rising / good-debt holding, so the real portfolio loss ≠ naive `category_weight × 20%`.
- **Goal cascade** — the shock ripples into `success_probability`, so you can also say "and 12 of them fall off their retirement goal as a result."

### Scenario ideas to pre-load as demo buttons

One shock, whole book, instant ranked fallout — each is a one-line Copilot query or a preset button:
- **Equity crash** — high/mid-risk equity −20% / −30%; who breaches tolerance.
- **Credit event** — bad-debt −15%; exposes "safe" debt portfolios that aren't.
- **Inflation / horizon squeeze** — returns −2% across the board; how many goals slip off track.
- **Gold rally** — tests who's *under*-diversified, not just over-exposed.
- **Concentration blow-up** — a top single fund −40%; who's dangerously concentrated.
- **Combo (2008-style)** — equity −35% + bad-debt −10% simultaneously, using `Σ` for the joint move.

The pattern in every case: *one scenario in, a ranked "who to call and why" list out.* That's the book-level differentiator, and it reuses the exact suitability reduction from `rank_book`.

---

## Data model

Mutual funds only. Everything else is cut. Eleven concerns, most of them tiny.

- **clients** — `id`, `name`, `age`, `risk_profile` (conservative / balanced / aggressive).
- **goals** — `id`, `client_id`, `name`, `target_amount`, `target_date`. *No SIP and no category list here* — a goal is funded by several funds, so both live per-fund (see `goal_holdings` and `sip_schedule`).
- **funds** — `id`, `name`, `amc`, `scheme_code` (optional AMFI code), `category` (one of the fourteen tags). The only instrument type. The `category` is the bridge from real funds → the engine's return dynamics.
- **nav_history** — `fund_id`, `date`, `nav`. PK `(fund_id, date)`. The daily price series that powers valuation, portfolio-over-time, *and* the derived assumptions. Seed it from AMFI's daily NAV dump.
- **transactions** — `id`, `client_id`, `fund_id`, `date`, `type` (buy / redeem ), `units`, `nav`, `amount` (= `units × nav`). *The source of truth for holdings*, entered via natural language (`add_transactions`). Buys/switch-in add units, redeem/switch-out subtract.
- **goal_holdings** — `(client_id, fund_id)` → `goal_id`. Tags each of a client's fund holdings to the goal it funds; a goal is funded by several funds. The engine derives a goal's category set by joining through `funds.category`. Both current holding value and future SIPs into that fund count toward the mapped goal.
- **sip_schedule** — per `(client_id, fund_id)`: `monthly_amount`, `stepup_rate`, `active`. The **forward-looking** contributions the engine projects — the future counterpart to `transactions`. The goal is inherited from `goal_holdings`, not stored again here.
- **holdings** *(derived, not stored)* — per `(client_id, fund_id)`: `units = Σ signed units`, `value = units × latest nav`. Roll up by `category` → the vector the engine reads. Compute as a view/query; don't materialize.
- **assumptions** *(derived nightly)* — per `category`: `mu`, `sigma`; plus the `Σ` covariance matrix (14×14). Computed from `nav_history` (see "Assumptions are derived from NAV history"). Keep a hardcoded fallback.
- **baseline_runs** *(the overnight sim cache)* — `client_id`, `as_of_date`, `seed`, `n_paths`, per-goal `success_prob` + terminal percentiles, per-client `VaR` / `CVaR` / `max_drawdown` / `suitability_mismatch` / `risk_score` (JSON is fine). `run_whatif` reads baseline from here so only the delta re-runs.
- **portfolio_value_history** *(derived, not stored)* — reconstruct any date's value from `transactions` (units held on that date) × `nav_history` (NAV that date). Feeds the Holdings chart.

That's enough to run all four numbers (goal probability, VaR/CVaR, what-if, book radar) plus real valuation and data-derived assumptions.

---

## Explicitly cut (and why)

Dropped because they don't move the demo or the pitch:

- **Every instrument except mutual funds** — no stocks, FDs, bonds, gold coins, cash accounts, insurance, loans. One instrument type, one NAV series shape, one clean model. Real-world messiness is not the demo.
- **Cost-basis / FIFO / tax-lot accounting** — holdings are units × latest NAV; we never compute realized gains. No tax pipeline.
- **Drift/rebalance pipeline** — deterministic; skip unless time is left.
- **Scheduled digests + event alerts infrastructure** — replace with a "Run book analysis" button; the ranked list is the point, the cron isn't.
- **Open-ended chat / general assistant** — the Copilot is scoped to the six tools above, not a free-form chatbot. Structured intent, not open conversation.
- **Client portal, real-time dashboard, multi-advisor, anomaly detection** — all out.

Rule of thumb: if it doesn't either (a) exercise the GPU, (b) exercise the LLM+tools, or (c) show up in the hero demo, it's cut.

---