# AdvisorOS

**A GPU-powered early-warning system and meeting co-pilot for financial advisors.**

AdvisorOS watches an advisor's entire book of mutual-fund portfolios with thousands of
simulated market futures per client, ranks who needs attention this week, and — live in a
meeting — re-runs the Monte Carlo engine in about a second to show whether a recommendation
actually helps. The advisor talks to it in plain English; a Gemini-driven agent reasons about
the request and calls tools that run on the simulation engine, then narrates the result as
probabilities and worst-cases rather than a single "assume 12%/yr" point estimate.

The advisor is the only user — there is no client portal.

> **The two themes it fuses:** an **LLM agent that reasons and calls tools** (the interface)
> on top of a **GPU Monte Carlo engine those tools run on** (the muscle).
> *"What if I bump Rahul's SIP by ₹5k and switch 10% from small-cap into a gilt fund?"* →
> the agent translates that sentence into a structured scenario, the engine re-runs the
> futures, and the answer comes back as *"retirement success goes 62% → 81%, worst-case
> shortfall drops from ₹18L to ₹4L."*

---

## Why it exists

Advisors manage hundreds of mutual-fund clients and hit two recurring pains: **meeting prep
is 20–30 minutes of manual spreadsheet work per client**, and **problems get noticed too
late**. Naive projections hide all downside. AdvisorOS replaces point estimates with
*probabilities and worst-cases*, computed fast enough to cover the whole book overnight and
to answer live in a meeting.

**One instrument type, on purpose.** The entire app tracks **mutual funds and nothing else** —
no stocks, FDs, cash instruments, insurance, or loans. One instrument type keeps the data
model tiny and the simulation honest.

---

## How it works

### The fourteen categories

Every fund carries one of **fourteen category tags** describing what its underlying assets
actually are. These categories — *not* individual funds — are what the engine simulates: the
covariance matrix is a **14×14** built across categories, and each fund inherits its
category's return dynamics. This keeps the math small no matter how many funds exist.

```
high_risk_equity   mid_risk_equity   low_risk_equity   international_equity
cash_equivalent    good_debt         bad_debt          gold
silver             aggressive_hybrid balanced_advantage conservative_hybrid
multi_asset        other
```

The tags collapse AMFI's ~40 SEBI sub-categories into buckets that are distinct in
risk/return behaviour. See [HACKATHON.md](HACKATHON.md) § "The one thing we track" for the
full mapping.

### Two levels of granularity

- **Funds** carry real NAVs → used for valuation, concentration, and portfolio-value-over-time.
- **Categories** carry the return dynamics (`mu`, `sigma`, `Σ`) → the engine simulates the
  14-vector.

### The market model is derived, not guessed

Because every fund has a daily NAV series, the assumptions layer (`mu`, `sigma`, `Σ`) is
**computed from NAV history**, not hardcoded — a real data story. It is recomputed nightly.
A hardcoded India-market fallback table ships so the engine runs even with thin NAV history
or no GPU. See [`engine/market.py`](backend/app/engine/market.py).

### The Monte Carlo engine

The one technical core, written once and reused for everything. Per client, per run: build
`Σ`, take its Cholesky factor `L`, draw a `(paths, categories)` block of correlated normals,
apply drift + volatility (lognormal step), compound forward month by month, inject SIP
contributions each step, and read the outputs off the terminal distribution:

- **Per goal** — success probability, terminal-value percentiles (P5/P50/P90), shortfall,
  required SIP to hit a target confidence.
- **Per client** — VaR, CVaR, simulated max drawdown, **suitability mismatch** (simulated
  downside − tolerable band), concentration flags.
- **Book roll-up** — the ranked "who to call first, and why" list.

The engine is a **pure function** of its inputs: same inputs + same seed → same numbers. It
lives in a standalone [`sim_kernel`](sim_kernel/) package so it can run in-process (NumPy,
local dev) *or* inside a RunPod serverless GPU worker (CuPy) — same code, same numbers,
either place. See [`sim_kernel/montecarlo.py`](sim_kernel/sim_kernel/montecarlo.py).

---

## Architecture

```
┌─────────────────────────┐      ┌──────────────────────────────┐      ┌──────────────┐
│  SvelteKit SPA           │      │  FastAPI backend             │      │  Postgres    │
│  (static, served by      │─────▶│  /api/*                      │─────▶│  funds, navs │
│   FastAPI in prod)       │      │                              │      │  clients,    │
│                          │      │  ┌────────────────────────┐  │      │  goals,      │
│  · Advisor Dashboard     │      │  │ LLM Copilot (Gemini    │  │      │  txns, SIPs, │
│  · Copilot Workspace     │      │  │  via pydantic-ai)      │  │      │  assumptions,│
│  · Client Detail         │      │  │  → 11-tool agent loop  │  │      │  baselines,  │
│  · Shareable Debrief     │      │  └───────────┬────────────┘  │      │  radar, ...  │
└─────────────────────────┘      │              │               │      └──────────────┘
                                  │  ┌───────────▼────────────┐  │
                                  │  │ app/gpu (the one seam) │  │      ┌──────────────┐
                                  │  └───────────┬────────────┘  │      │ RunPod GPU   │
                                  └──────────────┼───────────────┘      │ worker       │
                                        local numpy │ or RunPod ────────▶│ (sim_kernel  │
                                                    ▼                    │  + CuPy)     │
                                            sim_kernel.jobs              └──────────────┘
```

Three layers:

1. **Data layer** — Postgres. Transactions are the source of truth; holdings and
   portfolio-value-over-time are *derived* (views/queries), never materialized.
2. **Analytics layer** — the [`sim_kernel`](sim_kernel/) Monte Carlo engine over the
   14-category vector, plus a few deterministic checks (suitability, concentration,
   arithmetic stress).
3. **AI layer** — FastAPI endpoints wrapped as LLM tools; the Copilot agent loop calls them.

The backend never imports CuPy itself. [`app/gpu/client.py`](backend/app/gpu/client.py) is
the single seam: if `RUNPOD_API_KEY` + `RUNPOD_ENDPOINT_ID` are set, jobs run on the GPU
worker; otherwise they degrade to local NumPy in-process. Callers get the identical result
dict either way.

---

## Repository layout

```
advisor/
├─ backend/                  # FastAPI app + data + scripts
│  ├─ app/
│  │  ├─ main.py             # app assembly, /api router, SPA fallback, scheduler lifecycle
│  │  ├─ config.py           # env-driven settings (single source of truth)
│  │  ├─ models.py           # SQLAlchemy ORM tables + views
│  │  ├─ db.py               # engine, session, database bootstrap
│  │  ├─ scheduler.py        # APScheduler: nightly NAV refresh (12:40 IST) + book run (13:00 IST)
│  │  ├─ api/                # HTTP routers: clients, book, copilot, conversations, debrief
│  │  ├─ engine/             # DB→ClientState loader, market model, history, radar status
│  │  ├─ gpu/                # the RunPod-vs-local-numpy seam (client.py, runpod_client.py)
│  │  ├─ llm/                # Copilot agent loop, NL txn parser, debrief, insights, provider
│  │  ├─ tools/              # the tool implementations the agent calls (impl.py)
│  │  └─ tasks/              # nightly book analysis, NAV refresh
│  └─ scripts/              # seed.py, gen_clients.py, load_navs.py (+ fund/NAV CSVs)
├─ sim_kernel/               # standalone Monte Carlo engine (no web/DB deps)
│  └─ sim_kernel/            # categories, state, backend (xp swap), montecarlo, pipelines,
│                            #   whatif, jobs (the RunPod job contract)
├─ gpu_worker/               # RunPod serverless image; handler.py dispatches into sim_kernel.jobs
├─ frontend/                 # SvelteKit SPA (Svelte 5, Tailwind 4, static adapter)
├─ Dockerfile                # single image: builds the SPA, serves it + the API same-origin
├─ HACKATHON.md              # product scope & rationale
└─ IMPLEMENTATION.md         # original detailed build plan
```

---

## The AI Copilot

The Copilot is a **Gemini agent** (via `pydantic-ai`) scoped to a fixed tool set — structured
intent, not open chat. The model never does the math itself; it decides *which* tool to call
with *what* arguments, then narrates the result. Tool calls are surfaced to the UI as a
visible trace so you can see the orchestration, not just the answer.

Tools the agent can call ([`app/tools/impl.py`](backend/app/tools/impl.py), wired in
[`app/llm/copilot.py`](backend/app/llm/copilot.py)):

| Tool | What it does |
|---|---|
| `query_book` | Find clients matching criteria (off-track, over-exposed, by risk profile, by category exposure) |
| `get_client_brief` | Pull the latest per-client analysis (goal probabilities, risk, suitability) |
| `run_whatif` | Re-simulate one client with a lever change → before/after probability + tail risk |
| `project_portfolio` | Year-by-year P5/P50/P90 portfolio value under a scenario |
| `stress_book` | Apply one market shock across the whole book → who breaches tolerance |
| `rank_book` | The suitability-mismatch call list |
| `rank_goal_shortfalls` | Rank goals most in danger of falling short |
| `get_book_insights` | LLM-authored book-level insight cards |
| `book_trend` | Book-wide risk/allocation trend over a lookback window |
| `add_transactions` | Parse plain-English fund activity into structured rows for the advisor to confirm before commit |
| `run_sql` | Escape hatch for read queries the structured tools don't cover |

**Conversational what-if** and **book stress** are the two hero moments — the same surface, a
message in and a rich card back — and they're the clearest reason the workload wants a GPU:
one scenario, hundreds of clients, sub-second.

---

## The screens

- **Advisor Dashboard** (`/`) — the landing page: an LLM-narrated briefing + insight cards,
  an urgency-grouped action feed ("who do I call first, and why"), and at-a-glance
  visualizations (risk quadrant, book trend).
- **Copilot Workspace** (`/advisor`) — the conversational command center. The tool loop
  renders its answer + visible tool-call trace inline; what-if and stress come back as
  interactive cards.
- **Client Detail** (`/clients/[id]`) — profile & goals, natural-language transaction entry
  (parse → confirm → commit), derived holdings with category allocation and concentration
  flags, and a portfolio-value-over-time chart.
- **Shareable Debrief** (`/share/[token]`) — a persisted, publicly linkable client one-pager
  generated by the Copilot loop. This is the app's only public, unauthenticated route.

---

## Getting started

### Prerequisites

- **Python ≥ 3.14** and [`uv`](https://docs.astral.sh/uv/)
- **Node.js 22+**
- **PostgreSQL** (a reachable instance; `seed.py` can create the database itself)
- A **Gemini API key** for the Copilot (the app runs read-only screens without it)
- *(optional)* a **RunPod** serverless endpoint for GPU execution — omit it and everything
  runs on local NumPy

### 1. Configure the backend

Create `backend/.env`:

```sh
POSTGRES_URL=postgresql://user:pass@host:5432/advisor
GEMINI_API_KEY=...                 # or LLM_API_KEY; Copilot/debrief need this
# LLM_MODEL=gemini-3.5-flash       # optional override

# Optional — GPU execution via RunPod. Unset → local numpy in-process.
RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...

# Optional — object storage (GCS via its S3-compatible API) for shared debriefs.
ACCESS_KEY=...
ACCESS_SECRET=...
```

See [`app/config.py`](backend/app/config.py) for every knob (Monte Carlo path counts, seed,
confidence, VaR percentile, GPU switch).

### 2. Seed the database

```sh
cd backend
uv sync
uv run python -m scripts.seed
```

This one-shot initializer (safe to re-run) creates the database and tables, loads real funds
and ~1.8M rows of historical NAV from the CSVs in `scripts/`, tops up NAVs to today from
AMFI's live feed, generates a deterministic pool of 150 synthetic clients with goals /
portfolios / SIPs, and runs the first whole-book analysis to fill the baseline caches. The
synthetic book deliberately plants edge cases (over-exposed conservatives, concentrated
positions) so the radar and stress tests light up.

### 3. Run the backend

```sh
cd backend
uv run fastapi dev main.py          # → http://localhost:8000, API under /api
```

Interactive API docs at `http://localhost:8000/api/docs`.

### 4. Run the frontend

```sh
cd frontend
npm install
npm run dev                          # → http://localhost:5173 (proxies to :8000/api)
```

---

## Production build (Docker)

A single image builds the SvelteKit SPA and serves it *same-origin* alongside the FastAPI
backend (SPA at `/`, API at `/api/*`). Build from the repo root so it can copy both
`frontend/` and the sibling `sim_kernel/` package:

```sh
docker build -t advisoros .
docker run --env-file backend/.env -p 8000:8000 advisoros
# → http://localhost:8000
```

```sh
docker buildx build \
  --provenance=false --sbom=false \
  -t asia-southeast1-docker.pkg.dev/project-97144aca-7cda-48e5-a37/advisor-gpu/advisor:1.14 \
  --push .
```

The GPU worker is a separate image pushed to a container registry and pointed at a RunPod
serverless endpoint — see [`gpu_worker/README.md`](gpu_worker/README.md) for build/push and
endpoint setup.

---

## Data model

Mutual funds only. Transactions are the source of truth; holdings and portfolio-value-over-time
are derived views, never materialized.

- **Input** — `funds`, `nav_history`, `clients`, `goals`, `transactions`, `sip_schedule`,
  `goal_holdings`, `assumptions` (+ `covariances`)
- **Derived / cache** — `latest_holdings` (view), `baseline_runs` (dated overnight sim cache),
  `radar_output` / `radar_snapshots`, `book_insights`
- **Copilot** — `copilot_conversations`, `copilot_messages`, `client_debriefs`

Full DDL rationale is in [IMPLEMENTATION.md](IMPLEMENTATION.md) § 3; the live schema is
[`backend/app/models.py`](backend/app/models.py).

---
