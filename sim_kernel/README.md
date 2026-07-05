# sim-kernel

The Monte Carlo engine, factored out so it can run either in-process (CPU, local dev) or
inside a RunPod serverless GPU worker — same code, same numbers, either place.

Zero dependency on Postgres, FastAPI, or any web framework. Everything here is a pure
function of plain arrays and JSON-serializable dicts.

- `categories.py` — the 14 category tags + index.
- `state.py` — `MarketModel` / `ClientState` / `GoalState`, the wire-format dataclasses,
  plus `to_payload()`/`from_payload()` (de)serialization.
- `backend.py` — the `xp` swap (cupy if `SIM_KERNEL_GPU=true` and importable, else numpy).
- `montecarlo.py` — `simulate()`, the core engine.
- `pipelines.py` — statistics read off a simulated distribution (goal probability,
  VaR/CVaR, required SIP, book-wide stress).
- `whatif.py` — the what-if lever transforms + before/after diff.
- `jobs.py` — the RunPod job contract: `run_job(job_type, payload)`. This is the only
  module `gpu_worker`'s handler calls, and the only module the backend's `app.gpu`
  facade needs to mirror for local (non-RunPod) execution.

See `../IMPLEMENTATION.md` and `../HACKATHON.md` for the product context.
