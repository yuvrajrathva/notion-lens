import threading
from typing import Dict

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
