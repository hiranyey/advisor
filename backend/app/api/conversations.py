"""Copilot conversation history — DB-backed chats (list / open / delete) + the
persistence helpers the /copilot endpoint uses to save each turn.

A conversation is a row in `copilot_conversations` with its turns in `copilot_messages`.
The DB is authoritative: history fed to the model on each turn is rebuilt from stored
messages (user turns replay their id-rewritten `sent` text), and a reopened chat re-renders
from the stored assistant `trace`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import CopilotConversation, CopilotMessage
from ..schemas import ConversationDetail, ConversationMessage, ConversationRow

router = APIRouter(prefix="/conversations", tags=["conversations"])

TITLE_MAX = 60


def _title_from(message: str) -> str:
    t = " ".join((message or "").split())
    if not t:
        return "New conversation"
    return t[:TITLE_MAX] + "…" if len(t) > TITLE_MAX else t


# ── Persistence helpers (used by api/copilot.py) ──────────────────────────────
def load_history(session: Session, conversation_id: int) -> list[dict]:
    """Rebuild the model-facing history from stored turns (skip errored turns)."""
    msgs = session.execute(
        select(CopilotMessage)
        .where(CopilotMessage.conversation_id == conversation_id)
        .order_by(CopilotMessage.id)
    ).scalars().all()
    out: list[dict] = []
    for m in msgs:
        if m.error:
            continue
        if m.role == "user":
            out.append({"role": "user", "content": m.sent or m.content or ""})
        elif m.role == "assistant" and m.content:
            out.append({"role": "assistant", "content": m.content})
    return out


def save_turn(
    session: Session,
    conversation_id: int | None,
    *,
    user_raw: str,
    user_sent: str,
    client_id: int | None,
    answer: str,
    trace: list | None,
    backend: str | None,
    elapsed_ms: float | None,
) -> int:
    """Persist one user→assistant turn. Creates the conversation on first turn (titled
    from the user's message) and bumps its updated_at. Returns the conversation id."""
    if conversation_id is None:
        conv = CopilotConversation(title=_title_from(user_raw), client_id=client_id)
        session.add(conv)
        session.flush()  # assign conv.id
        conversation_id = conv.id
    else:
        conv = session.get(CopilotConversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")

    session.add(CopilotMessage(
        conversation_id=conversation_id, role="user", content=user_raw, sent=user_sent,
    ))
    session.add(CopilotMessage(
        conversation_id=conversation_id, role="assistant", content=answer,
        trace=trace or [], backend=backend, elapsed_ms=elapsed_ms,
    ))
    session.execute(
        update(CopilotConversation)
        .where(CopilotConversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    session.commit()
    return conversation_id


# ── CRUD endpoints ────────────────────────────────────────────────────────────
@router.get("", response_model=list[ConversationRow])
def list_conversations(session: Session = Depends(get_session)) -> list[ConversationRow]:
    """The history sidebar: every conversation, most-recently-updated first."""
    rows = session.execute(
        select(
            CopilotConversation.id,
            CopilotConversation.title,
            CopilotConversation.client_id,
            CopilotConversation.updated_at,
            func.count(CopilotMessage.id).label("message_count"),
        )
        .outerjoin(CopilotMessage, CopilotMessage.conversation_id == CopilotConversation.id)
        .group_by(CopilotConversation.id)
        .order_by(CopilotConversation.updated_at.desc())
    ).all()
    return [
        ConversationRow(
            id=r.id, title=r.title, client_id=r.client_id,
            message_count=r.message_count, updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int, session: Session = Depends(get_session)
) -> ConversationDetail:
    """Open a conversation — its turns, in order, ready to re-render (trace included)."""
    conv = session.get(CopilotConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    msgs = session.execute(
        select(CopilotMessage)
        .where(CopilotMessage.conversation_id == conversation_id)
        .order_by(CopilotMessage.id)
    ).scalars().all()
    return ConversationDetail(
        id=conv.id, title=conv.title, client_id=conv.client_id,
        messages=[
            ConversationMessage(
                role=m.role, content=m.content, trace=m.trace or [],
                backend=m.backend,
                elapsed_ms=float(m.elapsed_ms) if m.elapsed_ms is not None else None,
                error=m.error,
            )
            for m in msgs
        ],
    )


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int, session: Session = Depends(get_session)
) -> dict:
    conv = session.get(CopilotConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    session.delete(conv)  # cascade drops its messages
    session.commit()
    return {"ok": True, "deleted": conversation_id}
