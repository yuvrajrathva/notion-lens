import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import repositories
import sync_service
import sync_status
from config import settings
from db import SessionLocal

_scheduler = AsyncIOScheduler()


async def sweep() -> None:
    """Periodic safety net: incrementally syncs any connection that hasn't run
    an automatic sync in the last AUTO_SYNC_INTERVAL_HOURS, independent of
    whether the user has opened the side panel."""
    session = SessionLocal()
    try:
        connections = repositories.list_all_connections(session)
    finally:
        session.close()

    now = datetime.now(timezone.utc)
    for connection in connections:
        if sync_status.should_auto_sync(str(connection.app_user_id), connection.last_synced_at, now):
            asyncio.create_task(sync_service.run_sync(connection.app_user_id))


def start_scheduler() -> None:
    _scheduler.add_job(
        sweep,
        "interval",
        minutes=settings.AUTO_SYNC_SWEEP_INTERVAL_MINUTES,
        id="daily_incremental_sync_sweep",
        next_run_time=datetime.now(),
    )
    _scheduler.start()


def shutdown_scheduler() -> None:
    _scheduler.shutdown(wait=False)
