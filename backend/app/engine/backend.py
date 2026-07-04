"""The one-line GPU swap.

Every array op in the engine goes through `xp` so flipping CPU->GPU is a single
import decision made here. `cupy` is only *attempted* when `IS_GPU_AVAILABLE=true`;
on a CPU box we don't even try to import it (it isn't installed), so the demo always
runs. `cupy.random`, `cupy.linalg.cholesky`, `@`, and `cupy.quantile` are drop-in for
their NumPy counterparts, so `montecarlo.py`/`market.py` need no branching.

Report `GPU` and time `simulate()` with `timer()` — that wall-time is part of the pitch.
"""

import time
from contextlib import contextmanager

from ..config import settings

if settings.is_gpu_available:
    try:
        import cupy as xp  # type: ignore

        GPU = True
    except ImportError:  # env claims GPU but CuPy missing — degrade, don't crash
        import numpy as xp

        GPU = False
        print("[engine] IS_GPU_AVAILABLE=true but CuPy not importable; using NumPy (CPU).")
else:
    import numpy as xp

    GPU = False

# Runtime label for API responses / logs.
BACKEND = "cupy (GPU)" if GPU else "numpy (CPU)"


def asnumpy(a):
    """Bring an array back to host memory. No-op on NumPy; `.get()`-equivalent on CuPy."""
    return xp.asnumpy(a) if GPU else a


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
