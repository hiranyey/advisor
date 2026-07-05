"""POST /copilot/jobs + GET /copilot/jobs/{id}/events — the Copilot Workspace endpoint.

One advisor message in; a job comes back immediately (some tool calls — Monte Carlo
sims — can run long enough to blow past an HTTP timeout if we wait for the whole turn
in one request/response). The browser then opens an SSE stream that emits each tool
call as it starts, each result as it resolves, the model's narration between tool
calls, and finally the full narrated answer with the complete trace. The heavy lifting
(the six-tool loop, the engine, the caches) lives in app.llm and app.tools; this is just
the HTTP seam plus the in-memory job bookkeeping.

Job state lives in a per-process dict — this deploys as a single uvicorn worker (see
Dockerfile), so that's safe; it will not survive a restart or a future multi-worker
deploy, which is fine for a live-progress view of an in-flight chat turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from ..config import settings
from ..db import SessionLocal, get_session
from ..engine import market
from ..llm.copilot import run_copilot
from ..schemas import CopilotRequest
from .conversations import load_history, save_turn

router = APIRouter(tags=["copilot"])
log = logging.getLogger(__name__)

JOB_TTL_SECONDS = 15 * 60


@dataclass
class JobState:
    events: list[dict] = field(default_factory=list)
    status: str = "running"  # running | done | error
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)


JOBS: dict[str, JobState] = {}


def _sweep_expired() -> None:
    now = time.monotonic()
    stale = [
        jid for jid, job in JOBS.items()
        if job.status != "running" and now - job.created_at > JOB_TTL_SECONDS
    ]
    for jid in stale:
        del JOBS[jid]


async def _push(job: JobState, event: dict) -> None:
    async with job.condition:
        job.events.append(event)
        if event["type"] in ("final", "error"):
            job.status = "done" if event["type"] == "final" else "error"
        job.condition.notify_all()


async def _run_job(
    job: JobState, *, message: str, display_message: str, history: list[dict],
    client_id: int | None, conversation_id: int | None,
) -> None:
    async def on_event(event: dict) -> None:
        await _push(job, event)

    def _resolve_model():
        return market.load_persisted(session) or market.resolve_market_model(session)

    session = SessionLocal()
    try:
        model = await run_in_threadpool(_resolve_model)
        out = await run_copilot(
            session=session, model=model, message=message, history=history,
            client_id=client_id, on_event=on_event,
        )
        conv_id = await run_in_threadpool(
            save_turn, session, conversation_id,
            user_raw=display_message, user_sent=message, client_id=client_id,
            answer=out["answer"], trace=out["trace"], backend=out["backend"],
            elapsed_ms=out["elapsed_ms"],
        )
        await _push(job, {
            "type": "final", "answer": out["answer"], "trace": out["trace"],
            "elapsed_ms": out["elapsed_ms"], "backend": out["backend"],
            "conversation_id": conv_id,
        })
    except Exception as exc:
        log.exception("copilot job failed")
        await _push(job, {"type": "error", "detail": str(exc)})
    finally:
        session.close()


@router.post("/copilot/jobs")
async def create_copilot_job(
    req: CopilotRequest, session: Session = Depends(get_session)
) -> dict:
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="Copilot unavailable: set GEMINI_API_KEY (or LLM_API_KEY) on the backend.",
        )

    _sweep_expired()

    # History is DB-authoritative: replay the stored turns of an existing conversation
    # (the client-sent `history` is only a fallback for a brand-new, unsaved chat). Off
    # the event loop since it's a sync DB call.
    if req.conversation_id is not None:
        history = await run_in_threadpool(load_history, session, req.conversation_id)
    else:
        history = [t.model_dump() for t in req.history]

    job = JobState()
    job_id = uuid.uuid4().hex
    JOBS[job_id] = job
    job.task = asyncio.create_task(_run_job(
        job,
        message=req.message,
        display_message=req.display_message or req.message,
        history=history,
        client_id=req.client_id,
        conversation_id=req.conversation_id,
    ))
    return {"job_id": job_id}


def _format_sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.get("/copilot/jobs/{job_id}/events")
async def stream_copilot_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found (or expired)")

    async def gen():
        idx = 0
        while True:
            async with job.condition:
                while idx == len(job.events) and job.status == "running":
                    await job.condition.wait()
                new_events = job.events[idx:]
                idx = len(job.events)
                done = job.status != "running"
            for event in new_events:
                yield _format_sse(event)
            if done:
                return

    return StreamingResponse(gen(), media_type="text/event-stream")
