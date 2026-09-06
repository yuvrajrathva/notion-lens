from datetime import datetime, timedelta, timezone

import sync_status


def setup_function():
    # sync_status keeps module-level in-memory state; reset between tests.
    sync_status._status.clear()


def test_never_synced_is_due():
    now = datetime.now(timezone.utc)
    assert sync_status.should_auto_sync("user-1", None, now) is True


def test_recently_synced_is_not_due():
    now = datetime.now(timezone.utc)
    last_synced_at = now - timedelta(hours=1)
    assert sync_status.should_auto_sync("user-1", last_synced_at, now) is False


def test_stale_sync_is_due():
    now = datetime.now(timezone.utc)
    last_synced_at = now - timedelta(hours=25)
    assert sync_status.should_auto_sync("user-1", last_synced_at, now) is True


def test_currently_running_is_not_due():
    now = datetime.now(timezone.utc)
    sync_status.set_status("user-1", state="running")
    assert sync_status.should_auto_sync("user-1", None, now) is False


def test_recent_error_is_not_retried_within_cooldown():
    now = datetime.now(timezone.utc)
    sync_status.set_status(
        "user-1", state="error", finished_at=(now - timedelta(minutes=10)).isoformat()
    )
    assert sync_status.should_auto_sync("user-1", None, now) is False


def test_error_outside_cooldown_is_retried():
    now = datetime.now(timezone.utc)
    sync_status.set_status(
        "user-1", state="error", finished_at=(now - timedelta(hours=2)).isoformat()
    )
    assert sync_status.should_auto_sync("user-1", None, now) is True
