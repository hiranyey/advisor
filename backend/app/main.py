"""FastAPI app + router registration + scheduler lifecycle.

The daily AMFI NAV refresh runs via APScheduler (see app.scheduler). A manual
trigger endpoint is exposed for testing and on-demand refreshes.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from app import scheduler
from app.api import book, clients, conversations, copilot, debrief
from app.api.copilot import JOBS
from app.db import Base, engine
from app.models import (
    BookInsight,
    ClientDebrief,
    CopilotConversation,
    CopilotMessage,
    RadarSnapshot,
)
from app.tasks.refresh_navs import refresh_navs


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables added after the initial seed exist (idempotent) so an already-seeded
    # DB gains them without a full reseed. seed.py create_all covers a fresh DB.
    Base.metadata.create_all(
        engine,
        tables=[
            CopilotConversation.__table__,
            CopilotMessage.__table__,
            RadarSnapshot.__table__,
            BookInsight.__table__,
            ClientDebrief.__table__,
        ],
    )
    # Columns added to an already-existing table after the initial seed — create_all
    # only creates whole tables, so these need an explicit (idempotent) patch.
    with engine.begin() as conn:
        conn.exec_driver_sql("alter table radar_output add column if not exists reason text")
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        # Don't abandon in-flight Copilot jobs on shutdown/redeploy — give them a
        # chance to finish, then cancel whatever's left.
        tasks = [
            job.task
            for jobs in (JOBS, debrief.JOBS)
            for job in jobs.values()
            if job.task and not job.task.done()
        ]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=10)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(
    title="AdvisorOS",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Allow the SvelteKit dev server (and any local origin) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes live under /api — the same origin also serves the built SvelteKit
# SPA at "/" (see the static mount below), so this prefix is what actually separates
# them, not just a proxy-facing root_path.
api = APIRouter(prefix="/api")

api.include_router(clients.router)
api.include_router(book.router)
api.include_router(copilot.router)
api.include_router(conversations.router)
api.include_router(debrief.router)


@api.get("/health")
def read_root():
    return {"service": "AdvisorOS", "status": "ok"}


@api.post("/admin/refresh-navs")
async def trigger_refresh():
    """Run the AMFI NAV refresh now (same job the 12:40 IST cron runs)."""
    stats = await run_in_threadpool(refresh_navs)
    return {"ok": True, **stats}


app.include_router(api)

# Built SvelteKit assets (see Dockerfile), copied in at this path. Unset for local
# `uv run fastapi dev` where the frontend runs on its own Vite dev server instead.
frontend_dist = os.getenv("FRONTEND_DIST")
if frontend_dist and Path(frontend_dist).is_dir():
    dist = Path(frontend_dist)
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc: StarletteHTTPException) -> Response:
        # The SPA owns client-side routing (e.g. /clients/123) so any unmatched,
        # non-API GET falls back to the app shell instead of a bare 404.
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(dist / "index.html")
