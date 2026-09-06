import threading
from datetime import datetime, timedelta
from typing import Dict, Optional

from config import settings

_lock = threading.Lock()
_status: Dict[str, dict] = {}


def set_status(app_user_id: str, **fields) -> None:
    with _lock:
        _status.setdefault(app_user_id, {}).update(fields)


def get_status(app_user_id: str) -> dict:
    with _lock:
        return dict(_status.get(app_user_id, {"state": "idle"}))


def is_running(app_user_id: str) -> bool:
    with _lock:
        return _status.get(app_user_id, {}).get("state") == "running"


def should_auto_sync(app_user_id: str, last_synced_at: Optional[datetime], now: datetime) -> bool:
    """Whether an automatic (on-app-open or scheduled) sync should fire for this
    connection right now: never more than once per AUTO_SYNC_INTERVAL_HOURS, never
    while one is already running, and not immediately retried after a failure."""
    status = get_status(app_user_id)

    if status.get("state") == "running":
        return False

    if status.get("state") == "error":
        finished_at = status.get("finished_at")
        if finished_at:
            finished_dt = datetime.fromisoformat(finished_at)
            cooldown = timedelta(hours=settings.AUTO_SYNC_ERROR_COOLDOWN_HOURS)
            if now - finished_dt < cooldown:
                return False

    if last_synced_at is None:
        return True

    return now - last_synced_at >= timedelta(hours=settings.AUTO_SYNC_INTERVAL_HOURS)
