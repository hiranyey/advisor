"""The single seam between backend business logic and GPU compute.

Mirrors the old `engine/backend.py`'s philosophy — callers never know which backend
actually ran `simulate()`. Here that choice is "local numpy in-process" (dev, or RunPod
not configured) vs "a RunPod serverless GPU worker" (production), but either way the
caller gets back the exact same `sim_kernel.jobs` result dict. This module's only job is
picking a side and converting `ClientState`/`MarketModel`/`Levers` objects to and from the
JSON payload shape both sides agree on.

Live, single-client jobs (`client_insights`, `whatif`) run sync (`/runsync`) — an advisor
is waiting on the response. The nightly `book_analysis` runs async (`/run` + poll) since
nobody is blocked on it and a whole-book pass can run for minutes.
"""

from __future__ import annotations

from sim_kernel.state import ClientState, MarketModel
from sim_kernel.whatif import Levers

from ..config import settings
from . import runpod_client


def _run(job_type: str, payload: dict, *, sync: bool = True) -> dict:
    if settings.runpod_configured:
        return runpod_client.run(job_type, payload, sync=sync)
    from sim_kernel.jobs import run_job
    return run_job(job_type, payload)


def backend_label() -> str:
    """Whole-app posture label (RunPod configured vs local numpy) — for contexts that
    report one backend for a turn that might not call the engine at all (e.g. the
    Copilot loop). Live per-job endpoints should prefer the `backend` key a job actually
    returns (`sim_kernel.jobs` echoes back where it really ran)."""
    return "cupy (GPU via RunPod)" if settings.runpod_configured else "numpy (CPU, local)"


def client_insights(
    state: ClientState, model: MarketModel, *,
    n_paths: int, required_sip_paths: int, seed: int, confidence: float,
) -> dict:
    """One round trip covering every goal plus the 1-year portfolio risk figure —
    including any `required_sip` bisections, which never leave this one call."""
    return _run("client_insights", {
        "model": model.to_payload(),
        "client": state.to_payload(),
        "n_paths": n_paths, "required_sip_paths": required_sip_paths,
        "seed": seed, "confidence": confidence,
    })


def whatif(state: ClientState, model: MarketModel, levers: Levers, *, n_paths: int, seed: int) -> dict:
    return _run("whatif", {
        "model": model.to_payload(), "client": state.to_payload(),
        "levers": levers.to_payload(), "n_paths": n_paths, "seed": seed,
    })


def book_analysis(states: list[ClientState], model: MarketModel, *, n_paths: int, seed: int) -> dict:
    """The whole book in one job, run async — a batch pass nobody is waiting on."""
    return _run("book_analysis", {
        "model": model.to_payload(),
        "clients": [s.to_payload() for s in states],
        "n_paths": n_paths, "seed": seed,
    }, sync=False)


def book_stress(
    states: list[ClientState], model: MarketModel, shock: dict, *, n_paths: int, seed: int,
) -> dict:
    """Monte Carlo book stress only. Deterministic stress never reaches this facade — it's
    plain weight×shock arithmetic with no GPU benefit; see `tools/impl.py:stress_book`."""
    return _run("book_stress", {
        "model": model.to_payload(),
        "clients": [
            {"id": s.id, "risk_profile": s.risk_profile, "holdings": s.holdings.tolist()}
            for s in states
        ],
        "shock": shock, "n_paths": n_paths, "seed": seed,
    })
