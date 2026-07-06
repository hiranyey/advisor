"""Shareable client debriefs — LLM-suggested questions + a Copilot-style job that
turns the chosen one into a persisted, publicly linkable one-pager.

Mirrors api/copilot.py's job+SSE shape exactly (own in-memory JOBS dict, same
event/streaming plumbing) but the turn itself is a single fresh question (no
conversation history) run through the same app.llm.copilot.run_copilot loop, and
the result is persisted to `client_debriefs` behind a random share token instead
of a conversation. `GET /debrief/{share_token}` is the one public, unauthenticated
route in the app — anyone with the link can view that one report, nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from ..config import settings
from ..db import SessionLocal, get_session
from ..engine import market
from ..llm.copilot import run_copilot
from ..llm.debrief import generate_suggestions
from ..models import Client, ClientDebrief
from ..schemas import (
    DebriefDetail,
    DebriefJobRequest,
    DebriefRow,
    DebriefSuggestionsResponse,
)

router = APIRouter(tags=["debrief"])
log = logging.getLogger(__name__)

JOB_TTL_SECONDS = 15 * 60


@dataclass
class JobState:
    events: list[dict] = field(default_factory=list)
    status: str = "running"  # running | done | error | cancelled
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
        if event["type"] in ("final", "error", "cancelled"):
            job.status = "done" if event["type"] == "final" else event["type"]
        job.condition.notify_all()


async def _run_job(job: JobState, *, client_id: int, question: str) -> None:
    async def on_event(event: dict) -> None:
        await _push(job, event)

    def _resolve_model():
        return market.load_persisted(session) or market.resolve_market_model(session)

    session = SessionLocal()
    try:
        client = session.get(Client, client_id)
        client_name = client.name if client else None
        # Hard-pin the turn to this one client. The book can contain same/similar
        # names (e.g. two "Kavya"s) — without this, a name mentioned in the question
        # (common, since suggestions are phrased using the client's own name) lets
        # query_book resolve to the wrong person and leak their data into a report
        # meant to be shared outside the firm. This overrides the base Copilot
        # instructions' "assume 'this client' unless another is named" behavior,
        # which is fine for an advisor chatting internally but not for a debrief.
        scoped_message = (
            f"This report is exclusively for client id {client_id}"
            + (f" ({client_name})" if client_name else "")
            + ". Answer using ONLY this client's own data — never look up, call a "
            "tool for, mention, or compare another client, even if the question "
            "names them or another client shares a similar/identical name. Treat "
            f"every name below as referring to client id {client_id}.\n\n"
            f"Question: {question}"
        )
        model = await run_in_threadpool(_resolve_model)
        out = await run_copilot(
            session=session, model=model, message=scoped_message, history=None,
            client_id=client_id, on_event=on_event,
        )

        def _persist() -> str:
            token = secrets.token_urlsafe(24)
            session.add(ClientDebrief(
                client_id=client_id, share_token=token, question=question,
                answer=out["answer"], trace=out["trace"], backend=out["backend"],
                elapsed_ms=out["elapsed_ms"],
            ))
            session.commit()
            return token

        share_token = await run_in_threadpool(_persist)
        await _push(job, {
            "type": "final", "answer": out["answer"], "trace": out["trace"],
            "elapsed_ms": out["elapsed_ms"], "backend": out["backend"],
            "share_token": share_token,
        })
    except asyncio.CancelledError:
        await _push(job, {"type": "cancelled"})
    except Exception as exc:
        log.exception("debrief job failed")
        await _push(job, {"type": "error", "detail": str(exc)})
    finally:
        session.close()


@router.get("/clients/{client_id}/debrief/suggestions", response_model=DebriefSuggestionsResponse)
async def get_debrief_suggestions(
    client_id: int, session: Session = Depends(get_session)
) -> DebriefSuggestionsResponse:
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="Debrief unavailable: set GEMINI_API_KEY (or LLM_API_KEY) on the backend.",
        )
    if session.get(Client, client_id) is None:
        raise HTTPException(status_code=404, detail="client not found")

    suggestions = await generate_suggestions(session, client_id)
    return DebriefSuggestionsResponse(client_id=client_id, suggestions=suggestions)


@router.post("/clients/{client_id}/debrief/jobs")
async def create_debrief_job(
    client_id: int, req: DebriefJobRequest, session: Session = Depends(get_session)
) -> dict:
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="Debrief unavailable: set GEMINI_API_KEY (or LLM_API_KEY) on the backend.",
        )
    if session.get(Client, client_id) is None:
        raise HTTPException(status_code=404, detail="client not found")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    _sweep_expired()

    job = JobState()
    job_id = uuid.uuid4().hex
    JOBS[job_id] = job
    job.task = asyncio.create_task(_run_job(job, client_id=client_id, question=req.question))
    return {"job_id": job_id}


@router.delete("/debrief/jobs/{job_id}")
async def cancel_debrief_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found (or expired)")
    if job.task and not job.task.done():
        job.task.cancel()
    return {"status": "cancelling"}


def _format_sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.get("/debrief/jobs/{job_id}/events")
async def stream_debrief_job(job_id: str):
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


@router.get("/clients/{client_id}/debriefs", response_model=list[DebriefRow])
def list_debriefs(client_id: int, session: Session = Depends(get_session)) -> list[DebriefRow]:
    """Past reports for this client, newest first — so a generated link isn't lost
    if the advisor closes the share panel without copying it."""
    rows = session.execute(
        select(ClientDebrief)
        .where(ClientDebrief.client_id == client_id)
        .order_by(ClientDebrief.id.desc())
    ).scalars().all()
    return [
        DebriefRow(
            id=r.id, question=r.question, share_token=r.share_token, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/debrief/{share_token}", response_model=DebriefDetail)
def get_debrief(share_token: str, session: Session = Depends(get_session)) -> DebriefDetail:
    """The public one-pager payload. `share_token` is the sole lookup key — this
    route is intentionally unauthenticated so the link can be handed to the client."""
    debrief = session.execute(
        select(ClientDebrief).where(ClientDebrief.share_token == share_token)
    ).scalar_one_or_none()
    if debrief is None:
        raise HTTPException(status_code=404, detail="report not found")

    client = session.get(Client, debrief.client_id)
    return DebriefDetail(
        client_id=debrief.client_id,
        client_name=client.name if client else "Client",
        question=debrief.question,
        answer=debrief.answer,
        trace=debrief.trace or [],
        backend=debrief.backend,
        elapsed_ms=float(debrief.elapsed_ms) if debrief.elapsed_ms is not None else None,
        created_at=debrief.created_at,
    )
