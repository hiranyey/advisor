"""FastAPI app + router registration + scheduler lifecycle.

The daily AMFI NAV refresh runs via APScheduler (see app.scheduler). A manual
trigger endpoint is exposed for testing and on-demand refreshes.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from app import scheduler
from app.tasks.refresh_navs import refresh_navs


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="AdvisorOS", lifespan=lifespan,root_path="/api")


@app.get("/")
def read_root():
    return {"service": "AdvisorOS", "status": "ok"}


@app.post("/admin/refresh-navs")
async def trigger_refresh():
    """Run the AMFI NAV refresh now (same job the 12:40 IST cron runs)."""
    stats = await run_in_threadpool(refresh_navs)
    return {"ok": True, **stats}
