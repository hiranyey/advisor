"""APScheduler setup — the daily AMFI NAV refresh at 12:40 IST.

BackgroundScheduler (threaded) so the sync refresh job never blocks the event loop.
Started/stopped from the FastAPI lifespan in `app.main`.
"""

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.tasks.baseline import run_book_analysis
from app.tasks.refresh_navs import refresh_navs

IST = ZoneInfo("Asia/Kolkata")

scheduler = BackgroundScheduler(timezone=IST)


def start() -> None:
    scheduler.add_job(
        refresh_navs,
        trigger=CronTrigger(hour=12, minute=40, timezone=IST),
        id="daily_nav_refresh",
        name="Daily AMFI NAV refresh (12:40 IST)",
        replace_existing=True,
        misfire_grace_time=3600,  # still run if the app was briefly down at 12:40
        coalesce=True,
    )
    # On the GPU box, re-derive the market model and re-score the whole book nightly,
    # just after the NAV refresh — each run appends a dated baseline snapshot. On CPU the
    # model is the static fallback, so there's nothing to re-derive; skip the job.
    if settings.is_gpu_available:
        scheduler.add_job(
            run_book_analysis,
            trigger=CronTrigger(hour=13, minute=0, timezone=IST),
            id="nightly_book_analysis",
            name="Nightly book analysis (13:00 IST, GPU)",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )
    scheduler.start()


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
