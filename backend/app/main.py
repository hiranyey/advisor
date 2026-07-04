"""FastAPI app + router registration + scheduler lifecycle.

The daily AMFI NAV refresh runs via APScheduler (see app.scheduler). A manual
trigger endpoint is exposed for testing and on-demand refreshes.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app import scheduler
from app.api import book, clients
from app.tasks.refresh_navs import refresh_navs


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="AdvisorOS", lifespan=lifespan, root_path="/api")

# Allow the SvelteKit dev server (and any local origin) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clients.router)
app.include_router(book.router)


@app.get("/")
def read_root():
    return {"service": "AdvisorOS", "status": "ok"}


@app.post("/admin/refresh-navs")
async def trigger_refresh():
    """Run the AMFI NAV refresh now (same job the 12:40 IST cron runs)."""
    stats = await run_in_threadpool(refresh_navs)
    return {"ok": True, **stats}
