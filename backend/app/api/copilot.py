"""POST /copilot — the Copilot Workspace endpoint.

One advisor message in (plus prior turns + an optional client-in-context), one narrated
answer out, with the full tool-call trace so the frontend can render each tool result as a
card. The heavy lifting (the six-tool loop, the engine, the caches) lives in app.llm and
app.tools; this is just the HTTP seam. Runs as a sync endpoint so FastAPI executes it in a
threadpool — the pydantic-ai `run_sync` loop and the sync DB session stay off the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..engine import market
from ..llm.copilot import run_copilot
from ..llm.provider import LLMNotConfigured
from ..schemas import CopilotRequest, CopilotResponse
from .conversations import load_history, save_turn

router = APIRouter(tags=["copilot"])


@router.post("/copilot", response_model=CopilotResponse)
def copilot(req: CopilotRequest, session: Session = Depends(get_session)) -> CopilotResponse:
    if not settings.llm_configured:
        raise HTTPException(
            status_code=503,
            detail="Copilot unavailable: set GEMINI_API_KEY (or LLM_API_KEY) on the backend.",
        )

    # History is DB-authoritative: replay the stored turns of an existing conversation
    # (the client-sent `history` is only a fallback for a brand-new, unsaved chat).
    if req.conversation_id is not None:
        history = load_history(session, req.conversation_id)
    else:
        history = [t.model_dump() for t in req.history]

    model = market.load_persisted(session) or market.resolve_market_model(session)
    try:
        out = run_copilot(
            session=session,
            model=model,
            message=req.message,
            history=history,
            client_id=req.client_id,
        )
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    conversation_id = save_turn(
        session,
        req.conversation_id,
        user_raw=req.display_message or req.message,
        user_sent=req.message,
        client_id=req.client_id,
        answer=out["answer"],
        trace=out["trace"],
        backend=out["backend"],
        elapsed_ms=out["elapsed_ms"],
    )
    return CopilotResponse(**out, conversation_id=conversation_id)
