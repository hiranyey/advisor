"""The one-line GPU swap.

Every array op in the engine goes through `xp` so flipping CPU->GPU is a single import
decision made here. `cupy` is only *attempted* when `SIM_KERNEL_GPU=true`; on a CPU box
we don't even try to import it (it isn't installed), so local dev always runs. The RunPod
worker image sets `SIM_KERNEL_GPU=true`. `cupy.random`, `cupy.linalg.cholesky`, `@`, and
`cupy.quantile` are drop-in for their NumPy counterparts, so `montecarlo.py`/`pipelines.py`
need no branching.

Report `GPU` and time `simulate()` with `timer()` — that wall-time is part of the pitch.

This module intentionally reads its own env var rather than importing the backend app's
`config.Settings` — sim_kernel has zero dependency on Postgres/FastAPI/dotenv so it can be
copied into a minimal RunPod worker image unchanged.
"""

import os
import time
from contextlib import contextmanager


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


if _env_bool("SIM_KERNEL_GPU"):
    try:
        import cupy as xp  # type: ignore

        GPU = True
    except ImportError:  # env claims GPU but CuPy missing — degrade, don't crash
        import numpy as xp

        GPU = False
        print("[sim_kernel] SIM_KERNEL_GPU=true but CuPy not importable; using NumPy (CPU).")
else:
    import numpy as xp

    GPU = False

# Runtime label for API responses / logs.
BACKEND = "cupy (GPU)" if GPU else "numpy (CPU)"


def asnumpy(a):
    """Bring an array back to host memory. No-op on NumPy; `.get()`-equivalent on CuPy."""
    return xp.asnumpy(a) if GPU else a


def gpu_free_bytes() -> int | None:
    """Free device memory in bytes, or `None` off-GPU. Used to size batches so a big
    whole-book job (`simulate_batch`) doesn't request more than the device actually has —
    host RAM for the numpy path isn't a comparable constraint, so this is GPU-only."""
    if not GPU:
        return None
    free, _total = xp.cuda.Device().mem_info
    return int(free)


@contextmanager
def timer():
    """`with timer() as t: ...; t()` -> elapsed seconds. GPU-aware (syncs the stream)."""
    if GPU:
        xp.cuda.Stream.null.synchronize()
    start = time.perf_counter()
    elapsed = {"s": 0.0}
    try:
        yield lambda: elapsed["s"]
    finally:
        if GPU:
            xp.cuda.Stream.null.synchronize()
        elapsed["s"] = time.perf_counter() - start
