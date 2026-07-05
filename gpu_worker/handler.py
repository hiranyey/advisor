"""RunPod serverless entrypoint.

Deliberately thin: every actual decision (which job, what math) lives in `sim_kernel`,
which is shared verbatim with the main backend so "ran locally" and "ran on RunPod" are
never two implementations to keep in sync.

Job body shape (set by `app/gpu/runpod_client.py` on the backend):
    {"input": {"type": "client_insights" | "whatif" | "book_analysis" | "book_stress",
               "payload": {...}}}
"""

import runpod

from sim_kernel.jobs import run_job


def handler(job):
    inp = job["input"]
    return run_job(inp["type"], inp["payload"])


runpod.serverless.start({"handler": handler})
