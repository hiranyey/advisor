"""Thin HTTP client for RunPod serverless.

Every job payload (a client's 14-vectors + goals, or a book's worth of them) travels as
the POST body directly — no object storage. RunPod's limits are 10MB on `/run` and 20MB
on `/runsync`; a whole book of a few hundred clients as JSON floats is still low-single-
digit MB, so this is simple and fine. If the book ever grows past that, the fix is
chunking `book_analysis`'s client list across multiple calls (see `app/gpu/client.py`),
not object storage.

`sync=True` (`/runsync`) blocks for the result inline — used for live single-client
paths (client_insights, whatif) where an advisor is waiting on the response. `sync=False`
(`/run` + poll) is for the nightly book job, which can run for minutes and nobody is
blocked on.
"""

from __future__ import annotations

import time

import httpx

from ..config import settings

_BASE = "https://api.runpod.ai/v2"


class RunpodJobError(RuntimeError):
    pass


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.runpod_api_key}"}


def _unwrap(data: dict) -> dict:
    if data.get("status") == "FAILED":
        raise RunpodJobError(f"RunPod job failed: {data.get('error') or data}")
    return data["output"]


def run(job_type: str, payload: dict, *, sync: bool = True, timeout: float = 900.0) -> dict:
    """POST one job, return its `output` dict. Raises RunpodJobError on a FAILED job."""
    op = "runsync" if sync else "run"
    body = {"input": {"type": job_type, "payload": payload}}
    with httpx.Client(timeout=min(timeout, 90.0)) as http:
        resp = http.post(f"{_BASE}/{settings.runpod_endpoint_id}/{op}", json=body, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    if sync:
        return _unwrap(data)
    return _poll(data["id"], timeout=timeout)


def _poll(job_id: str, *, timeout: float, interval: float = 2.0) -> dict:
    status_url = f"{_BASE}/{settings.runpod_endpoint_id}/status/{job_id}"
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=30.0) as http:
        while time.monotonic() < deadline:
            data = http.get(status_url, headers=_headers()).json()
            if data.get("status") in ("COMPLETED", "FAILED"):
                return _unwrap(data)
            time.sleep(interval)
    raise TimeoutError(f"RunPod job {job_id} did not finish within {timeout}s")
